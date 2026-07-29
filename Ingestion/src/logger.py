
import uuid
from datetime import datetime
import logging
import json


class Logger:

    @classmethod
    def get_logger(cls, name):
        formatter = logging.Formatter(fmt='%(asctime)s - %(levelname)s - %(module)s - %(message)s')
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        logger = logging.getLogger(name)
        logger.setLevel(logging.DEBUG)
        logger.addHandler(handler)
        return logger

    @classmethod
    def get_save_logger(cls, name, server, database, username, password, table_name, driver, connection_string, container_name):
        logger = logging.getLogger(name)
        logger.setLevel(logging.DEBUG)
        logger.addHandler(azure_sql_handler)
        logger.addHandler(azure_blob_handler)
        return logger