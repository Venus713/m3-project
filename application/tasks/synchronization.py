from typing import List

from celery import Celery
from flask import current_app

from application.logging import logger
from application.utils import chunkify
from application.db_extension.models import PipelineSequence
from .processor import ProductProcessor, Product, SourceAttributeValue, SourceReview
from .pipeline import execute_pipeline

celery = Celery(__name__, autofinalize=False)

from celery import group

@celery.task(bind=True)
def add(self, x, y):
    return x + y


@celery.task(bind=True)
def sample_task(self):
    '''sample task that sleeps 5 seconds then returns the current datetime'''
    import time, datetime
    time.sleep(5)
    return datetime.datetime.now().isoformat()

def get_test_products():
    import csv
    root = current_app.config['BASE_PATH']
    with open(root / 'tools/products.csv', 'r') as f:
        rdr = csv.DictReader(f)
        return [r for r in rdr]

def get_products_for_source_id(source_id: int) -> List[Product]:
    if source_id == 12345:
        products = get_test_products()
    else:
        raise NotImplementedError(
            'You only able to use test source_id 12345 yet'
        )
    return [Product.from_raw(source_id, product) for product in
            products]


class BulkAdder:
    def __init__(self, model, threshold=10000):
        self._model = model
        self._threshold = threshold
        self._data = []

    def add(self, data):
        self._data.append(data)
        if len(self._data) == self._threshold:
            self.flush()

    def flush(self):
        self._model.bulk_insert_do_nothing(
            self._data
        )
        self._data = []


@celery.task(bind=True, name='tasks.process_product_list')
def process_product_list_task(self,
                              chunk: List[Product]):
    sav_bulk_adder = BulkAdder(SourceAttributeValue)
    review_bulk_adder = BulkAdder(SourceReview)
    processor = ProductProcessor(sav_bulk_adder=sav_bulk_adder,
                                 review_bulk_adder=review_bulk_adder)
    for product in chunk:
        processor.process(product)

    sav_bulk_adder.flush()
    review_bulk_adder.flush()

@celery.task(bind=True)
def execute_pipeline_task(self, source_id):
    sequence_id = PipelineSequence.get_latest_sequence_id(
        source_id
    )

    execute_pipeline(source_id, sequence_id)


logger.info('start_synchronization')


def start_synchronization(source_id: int) -> str:
    logger.info('start_synchronization')
    print('start synchronization')
    converted_products = get_products_for_source_id(source_id)
    chunks = list(chunkify(converted_products, 100))
    logger.info(f'{len(chunks)} chunks of products')
    logger.info('Creating job group')
    #job = group(
    #    process_product_list_task.s(chunk) for chunk in chunks
    #) | execute_pipeline_task.si(source_id)
    job = group(
        process_product_list_task.s(chunk) for chunk in chunks
    )
    logger.info('Calling job.delay()')
    task = job.delay()
    return task.id
