import io
import logging
from functools import partial
from hashlib import sha1

from django.conf import settings
from django.utils.functional import cached_property
from django.utils.module_loading import import_string
from rest_framework.parsers import JSONParser
from pika import BlockingConnection, URLParameters
from pika.exceptions import AMQPConnectionError
from tenacity import retry, retry_if_exception_type, wait_fixed

from pik.bus.mdm import mdm_event_captor
from pik.utils.sentry import capture_exception
from pik.utils.case_utils import underscorize
from pik.api.exceptions import extract_exception_data
from pik.core.shortcuts import update_or_create_object

from .models import PIKMessageException


logger = logging.getLogger(__name__)


class BusQueueNotFound(Exception):
    pass


def log_after_retry_connect(retry_state):
    logger.warning(
        'Connecting to RabbitMQ. Attempt number: %s',
        retry_state.attempt_number)


class MessageConsumer:
    RECONNECT_WAIT = 1
    PREFETCH_COUNT = 1

    _consumer_name = None
    _connection_url = None
    _queues = None
    _channel = None

    def __init__(self, connection_url, consumer_name, queues, event_captor):
        self._consumer_name = consumer_name
        self._connection_url = connection_url
        self._queues = queues
        self._event_captor = event_captor

    def consume(self):
        self._connect()
        self._config_channel()
        self._bind_queues()
        self._channel.start_consuming()

    @retry(
        wait=wait_fixed(RECONNECT_WAIT),
        retry=retry_if_exception_type(AMQPConnectionError),
        after=log_after_retry_connect)
    def _connect(self):
        self._channel = BlockingConnection(URLParameters(
            self._connection_url)).channel()

    def _config_channel(self):
        self._channel.basic_qos(prefetch_count=self.PREFETCH_COUNT)

    def _bind_queues(self):
        for queue in self._queues:
            logger.info('Starting %s queue consumer', queue)
            self._channel.basic_consume(
                on_message_callback=partial(self._handle_message, queue=queue),
                queue=queue)

    def _handle_message(self, channel, method, properties, body, queue):
        logger.info(
            'Handling %s bytes message from %s queue', len(body), queue)
        handler = MessageHandler(body, queue, mdm_event_captor)

        try:
            envelope = handler.envelope
        except Exception as error:  # noqa: too-broad-except
            self._capture_event({}, success=False, error=error)
            channel.basic_nack(delivery_tag=method.delivery_tag)
            return
        else:
            self._capture_event(envelope, success=True, error=None)

        handler.handle()
        channel.basic_ack(delivery_tag=method.delivery_tag)

    def _capture_event(self, envelope, **kwargs):
        self._event_captor.capture(
            event='consumption',
            entity_type=envelope.get('message', {}).get('type'),
            entity_guid=envelope.get('message', {}).get('guid'),
            transactionGUID=envelope.get('headers', {}).get('transactionGUID'),
            transactionMessageCount=envelope.get(
                'headers', {}).get('transactionMessageCount'),
            **kwargs)


class QueueSerializerMissingException(Exception):
    pass


class MessageHandler:
    parser_class = JSONParser

    _payload = None
    _parsed_payload = None
    _body = None
    _queue = None
    _serializers = None
    _exc_data = None

    def __init__(self, body, queue, event_captor):
        self._body = body
        self._queue = queue
        self._event_captor = event_captor

    def handle(self):
        try:
            self._fetch_payload()
            self._prepare_payload()
            self._update_instance()
            self._process_dependants()
            self._capture_event(success=True, error=None)
            return True
        except Exception as error:  # noqa: too-broad-except
            self._capture_event(success=False, error=error)
            self._capture_exception(error)
            return False

    @cached_property
    def envelope(self):
        return self.parser_class().parse(io.BytesIO(self._body))

    def _fetch_payload(self):
        self._payload = self.envelope['message']

    def _prepare_payload(self):
        self._payload = underscorize(self._payload)

        if hasattr(self._serializer_class, 'underscorize_hook'):
            self._payload = self._serializer_class.underscorize_hook(
                self._payload)

    def _update_instance(self):
        self._serializer.is_valid(raise_exception=True)
        self._serializer.save()

    @cached_property
    def _serializer(self):
        return self._serializer_class(self._instance, self._payload)

    @cached_property
    def _instance(self):
        try:
            return self._queryset.get(uid=self._payload['guid'])
        except self._model.DoesNotExist:
            return self._model(uid=self._payload['guid'])

    @property
    def _model(self):
        return self._serializer_class.Meta.model

    @property
    def _queryset(self):
        return getattr(self._model, 'all_objects', self._model.objects)

    @property
    def _serializer_class(self):
        return self._get_serializer(self._queue)

    @classmethod
    def _get_serializer(cls, queue):
        """
            Queue name to serializer mapping dict `{queue:  serializer, ... }`

            We want to build it once and use forever, but building it on
                startup is redundant for other workers and tests
        """
        if cls._serializers is None:
            cls._serializers = {
                queue: import_string(serializer)
                for queue, serializer in settings.RABBITMQ_CONSUMES.items()
            }
        if not cls._serializers or queue not in cls._serializers:  # noqa: unsupported-membership-test
            raise QueueSerializerMissingException(
                f'Unable to find serializer for {queue}')
        return cls._serializers[queue]  # noqa: unsupported-membership-test

    def _process_dependants(self):
        dependants = PIKMessageException.objects.filter(**{
            f'dependencies__{self._payload["type"]}': self._payload["guid"]})
        for dependant in dependants:
            handler = self.__class__(
                dependant.message, dependant.queue, mdm_event_captor)
            if handler.handle():
                dependant.delete()

    def _capture_exception(self, exc):
        capture_exception(exc)
        self._exc_data = extract_exception_data(exc)

        if not isinstance(self._payload, dict) or 'guid' not in self._payload:
            self._capture_invalid_payload(exc)
            return

        self._capture_missing_dependencies()

    def _capture_invalid_payload(self, exc):
        uid = sha1(self._body).hexdigest()[:32]
        update_or_create_object(
            PIKMessageException, search_keys={
                'queue': self._queue,
                'uid': uid},
            uid=uid,
            queue=self._queue,
            message=self._body,
            exception=extract_exception_data(exc),
            exception_message=self._exc_data['message'],
            exception_type=self._exc_data['code']
        )

    def _capture_missing_dependencies(self):
        dependencies = {
            self._payload[field]['type']: self._payload[field]['guid']
            for field, errors in self._exc_data.get('detail', {}).items()
            for error in errors
            if error['code'] == 'does_not_exist'}

        # TODO: catch errors
        update_or_create_object(
            PIKMessageException, search_keys={
                'entity_uid': self._payload.get('guid'),
                'queue': self._queue},
            entity_uid=self._payload.get('guid'),
            queue=self._queue,
            message=self._body,
            exception=self._exc_data,
            exception_type=self._exc_data['code'],
            exception_message=self._exc_data['message'],
            dependencies=dependencies
        )

    def _capture_event(self, **kwargs):
        self._event_captor.capture(
            event='deserialization',
            entity_type=self.envelope.get('message', {}).get('type'),
            entity_guid=self.envelope.get('message', {}).get('guid'),
            transactionGUID=self.envelope.get(
                'headers', {}).get('transactionGUID'),
            transactionMessageCount=self.envelope.get(
                'headers', {}).get('transactionMessageCount'),
            **kwargs)
