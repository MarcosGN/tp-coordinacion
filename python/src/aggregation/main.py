import os
import logging
import bisect
import signal

from common import middleware, message_protocol, fruit_item

ID = int(os.environ["ID"])
MOM_HOST = os.environ["MOM_HOST"]
OUTPUT_QUEUE = os.environ["OUTPUT_QUEUE"]
SUM_AMOUNT = int(os.environ["SUM_AMOUNT"])
SUM_PREFIX = os.environ["SUM_PREFIX"]
AGGREGATION_AMOUNT = int(os.environ["AGGREGATION_AMOUNT"])
AGGREGATION_PREFIX = os.environ["AGGREGATION_PREFIX"]
TOP_SIZE = int(os.environ["TOP_SIZE"])


class AggregationFilter:

    def __init__(self):
        self.input_exchange = middleware.MessageMiddlewareExchangeRabbitMQ(
            MOM_HOST, AGGREGATION_PREFIX, [f"{AGGREGATION_PREFIX}_{ID}"]
        )
        self.output_queue = middleware.MessageMiddlewareQueueRabbitMQ(
            MOM_HOST, OUTPUT_QUEUE
        )
        self.fruit_tops = {}
        self.eof_counts = {}

        signal.signal(signal.SIGTERM, self._handle_sigterm)

    def _handle_sigterm(self, signum, frame):
        logging.info("Received SIGTERM")
        self.input_exchange.stop_consuming()

    def _process_data(self, client_id, fruit, amount):
        logging.info("Processing data message")
        if client_id not in self.fruit_tops:
            self.fruit_tops[client_id] = []

        fruit_top = self.fruit_tops[client_id]
        for i in range(len(fruit_top)):
            if fruit_top[i].fruit == fruit:
                updated = fruit_top[i] + fruit_item.FruitItem(fruit, amount)
                fruit_top.pop(i)
                bisect.insort(fruit_top, updated)
                return
        bisect.insort(fruit_top, fruit_item.FruitItem(fruit, amount))

    def _process_eof(self, client_id):
        self.eof_counts[client_id] = self.eof_counts.get(client_id, 0) + 1
        logging.info(f"EOF {self.eof_counts[client_id]}/{SUM_AMOUNT} for client {client_id}")

        if self.eof_counts[client_id] < SUM_AMOUNT:
            return

        del self.eof_counts[client_id]
        top = self.fruit_tops.pop(client_id, [])
        fruit_chunk = list(top[-TOP_SIZE:])
        fruit_chunk.reverse()
        partial_top = [(fi.fruit, fi.amount) for fi in fruit_chunk]
        self.output_queue.send(
            message_protocol.internal.serialize(client_id, partial_top)
        )

    def process_messsage(self, message, ack, nack):
        logging.info("Process message")
        client_id, fields = message_protocol.internal.deserialize(message)
        if len(fields) == 2:
            self._process_data(client_id, *fields)
        else:
            self._process_eof(client_id)
        ack()

    def start(self):
        self.input_exchange.start_consuming(self.process_messsage)


def main():
    logging.basicConfig(level=logging.INFO)
    aggregation_filter = AggregationFilter()
    aggregation_filter.start()
    return 0


if __name__ == "__main__":
    main()
