import os
import logging
import signal

from common import middleware, message_protocol

MOM_HOST = os.environ["MOM_HOST"]
INPUT_QUEUE = os.environ["INPUT_QUEUE"]
OUTPUT_QUEUE = os.environ["OUTPUT_QUEUE"]
AGGREGATION_AMOUNT = int(os.environ["AGGREGATION_AMOUNT"])
TOP_SIZE = int(os.environ["TOP_SIZE"])


class JoinFilter:

    def __init__(self):
        self.input_queue = middleware.MessageMiddlewareQueueRabbitMQ(
            MOM_HOST, INPUT_QUEUE
        )
        self.output_queue = middleware.MessageMiddlewareQueueRabbitMQ(
            MOM_HOST, OUTPUT_QUEUE
        )
        self.partial_tops = {}   # client_id -> [[fruit, amount], ...]
        self.top_counts = {}     # client_id -> int (tops parciales recibidos)

        signal.signal(signal.SIGTERM, self._handle_sigterm)

    def _handle_sigterm(self, signum, frame):
        logging.info("Received SIGTERM")
        self.input_queue.stop_consuming()

    def _merge_tops(self, client_id):
        # Junta todos los tops parciales, ordena y toma el top N global
        all_items = self.partial_tops.pop(client_id, [])
        all_items.sort(key=lambda x: x[1], reverse=True)
        return all_items[:TOP_SIZE]

    def _process_message(self, message, ack, nack):
        client_id, partial_top = message_protocol.internal.deserialize(message)

        # Acumula los items del top parcial
        if client_id not in self.partial_tops:
            self.partial_tops[client_id] = []
        self.partial_tops[client_id].extend(partial_top)

        self.top_counts[client_id] = self.top_counts.get(client_id, 0) + 1
        logging.info(f"Received partial top {self.top_counts[client_id]}/{AGGREGATION_AMOUNT} for client {client_id}")

        if self.top_counts[client_id] < AGGREGATION_AMOUNT:
            ack()
            return

        del self.top_counts[client_id]
        final_top = self._merge_tops(client_id)
        logging.info(f"Sending final top for client {client_id}: {final_top}")
        self.output_queue.send(
            message_protocol.internal.serialize(client_id, final_top)
        )
        ack()

    def start(self):
        self.input_queue.start_consuming(self._process_message)


def main():
    logging.basicConfig(level=logging.INFO)
    join_filter = JoinFilter()
    join_filter.start()
    return 0


if __name__ == "__main__":
    main()