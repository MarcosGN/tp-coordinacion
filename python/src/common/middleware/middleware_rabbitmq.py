import pika
from .middleware import MessageMiddlewareCloseError, MessageMiddlewareDisconnectedError, MessageMiddlewareMessageError, MessageMiddlewareQueue, MessageMiddlewareExchange

class MessageMiddlewareQueueRabbitMQ(MessageMiddlewareQueue):

    def __init__(self, host, queue_name):
        self._queue_name = queue_name
        try:
            self._connection = pika.BlockingConnection(pika.ConnectionParameters(host=host))
            self._channel = self._connection.channel()
            self._channel.queue_declare(queue=queue_name)
        except pika.exceptions.AMQPConnectionError as e:
            raise MessageMiddlewareDisconnectedError(f"Could not connect to RabbitMQ at {host}") from e
 
    def send(self, message):
        try:
            self._channel.basic_publish(
                exchange='',
                routing_key=self._queue_name,
                body=message
            )
        except pika.exceptions.AMQPConnectionError as e:
            raise MessageMiddlewareDisconnectedError("Connection lost while sending message") from e
        except Exception as e:
            raise MessageMiddlewareMessageError("Error sending message") from e
 
    def start_consuming(self, on_message_callback):
        def _callback(ch, method, properties, body):
            ack  = lambda: ch.basic_ack(delivery_tag=method.delivery_tag)
            nack = lambda: ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
            on_message_callback(body, ack, nack)
 
        try:
            self._channel.basic_consume(
                queue=self._queue_name,
                on_message_callback=_callback,
                auto_ack=False
            )
            self._channel.start_consuming()
        except pika.exceptions.AMQPConnectionError as e:
            raise MessageMiddlewareDisconnectedError("Connection lost while consuming") from e
        except Exception as e:
            raise MessageMiddlewareMessageError("Error while consuming messages") from e
 
    def stop_consuming(self):
        try:
            self._channel.stop_consuming()
        except pika.exceptions.AMQPConnectionError as e:
            raise MessageMiddlewareDisconnectedError("Connection lost while stopping consumption") from e
 
    def close(self):
        try:
            self._connection.close()
        except Exception as e:
            raise MessageMiddlewareCloseError("Error closing connection") from e

class MessageMiddlewareExchangeRabbitMQ(MessageMiddlewareExchange):
    
    def __init__(self, host, exchange_name, routing_keys):
        self._exchange_name = exchange_name
        self._routing_keys = routing_keys
        try:
            self._connection = pika.BlockingConnection(pika.ConnectionParameters(host=host))
            self._channel = self._connection.channel()
            self._channel.exchange_declare(exchange=exchange_name, exchange_type='direct')
            result = self._channel.queue_declare(queue='', exclusive=True)
            self._queue_name = result.method.queue
            for routing_key in routing_keys:
                self._channel.queue_bind(
                    exchange=self._exchange_name,
                    queue=self._queue_name,
                    routing_key=routing_key
                )
        except pika.exceptions.AMQPConnectionError as e:
            raise MessageMiddlewareDisconnectedError(f"Could not connect to RabbitMQ at {host}") from e
 
    def send(self, message):
        try:
            for routing_key in self._routing_keys:
                self._channel.basic_publish(
                    exchange=self._exchange_name,
                    routing_key=routing_key,
                    body=message
                )
        except pika.exceptions.AMQPConnectionError as e:
            raise MessageMiddlewareDisconnectedError("Connection lost while sending message") from e
        except Exception as e:
            raise MessageMiddlewareMessageError("Error sending message") from e
 
    def start_consuming(self, on_message_callback):
        def _callback(ch, method, properties, body):
            ack  = lambda: ch.basic_ack(delivery_tag=method.delivery_tag)
            nack = lambda: ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
            on_message_callback(body, ack, nack)
 
        try:
            self._channel.basic_consume(
                queue=self._queue_name,
                on_message_callback=_callback,
                auto_ack=False
            )
            self._channel.start_consuming()
        except pika.exceptions.AMQPConnectionError as e:
            raise MessageMiddlewareDisconnectedError("Connection lost while consuming") from e
        except Exception as e:
            raise MessageMiddlewareMessageError("Error while consuming messages") from e
 
    def stop_consuming(self):
        try:
            self._channel.stop_consuming()
        except pika.exceptions.AMQPConnectionError as e:
            raise MessageMiddlewareDisconnectedError("Connection lost while stopping consumption") from e
 
    def close(self):
        try:
            self._connection.close()
        except Exception as e:
            raise MessageMiddlewareCloseError("Error closing connection") from e