from pydoc import locate

from django.conf import settings


class ModelSerializerMixin:
    SERIALIZER_OFFSET = 0
    QUEUE_OR_EXCHANGE_OFFSET = 1

    MODEL_SERIALIZER = {
        locate(serializer).Meta.model.__name__: (locate(serializer), queue)
        for queue, serializer in settings.RABBITMQ_SERIALIZERS.items()
    }
