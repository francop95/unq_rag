import logging
import pandas as pd
from models.LanguageModels import LanguageModel
from configs.ReadConfig import ReadConfig
from contexts.ChromaConnector import ChromaConnection

# logging
logger = logging.getLogger('app.LanguageModel')

class ModelSingleton:
    """
    A class that loads configuration settings and models, and returns a data dictionary containing the settings.

    Attributes:
        botInstance (None): The instance of the class.

    Methods:
        getInstance(): Returns the instance of the class.
        initializeInstance(): Performs one-time loading of configurations and models, and returns a data dictionary
            containing the settings.
    """
    botInstance = None

    @classmethod
    def getInstance(cls):
        if not cls.botInstance:
            cls.botInstance = ModelSingleton.initializeInstance()
        return cls.botInstance

    def initializeInstance():
        """
        Performs one-time loading of configurations and models, and returns a data dictionary
        containing the settings.

        Returns:
            dict: A dictionary containing the initialized settings.

        Example:
            settings = initializeInstance()
        """
        logger.info("[ModelSingleton] Inside initializeInstance()")

        try:
            config_reader = ReadConfig()
            data = config_reader.getConfigSettings()

            # embedding llm
            embedding_llm = LanguageModel(data, model_type=data["llm_type"])
            logger.info("[ModelSingleton] loaded Embedding Model!")

            # context dataframes [default?]
            context_dataframe = pd.DataFrame()
            context_dataframes = [context_dataframe]

            # Create persistent chroma connection ONCE
            search_connector = ChromaConnection(data)
            # Force initial connect so it’s warm
            try:
                search_connector.connect()
                logger.info("[ModelSingleton] Chroma connection warm and ready")
            except Exception as e:
                logger.warning(f"[ModelSingleton] Could not warm Chroma: {e}")

            # return data
            data["embedding_model"] = embedding_llm
            data["context_dataframe"] = context_dataframe
            data["context_dataframes"] = context_dataframes
            data["search_connector"] = search_connector

            # set default values
            data["cache_found"] = False
            data["context_found"] = False

            logger.info("[ModelSingleton] Initialization successful!!")
        except Exception as e:
            logger.error(f"[ModelSingleton] Error initializing: {str(e)} ")

        # log keys in data
        logger.info(f"[ModelSingleton] Data Keys: {str(data.keys())}")
        logger.info(f"[ModelSingleton] Total keys in data: {str(len(data))}")

        return data
