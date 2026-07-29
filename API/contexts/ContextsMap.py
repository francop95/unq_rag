# load libraries
import os
import sys
import logging
import numpy as np
from contexts.ChromaConnector import ChromaConnection

# Get the path of the top-level directory
top_level_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# Add the top-level directory to sys.path
sys.path.append(top_level_dir)

# append other directories to sys.path
sys.path.append(os.path.join(top_level_dir, "models"))
sys.path.append(os.path.join(top_level_dir, "contexts"))




# logging
logger = logging.getLogger('app.GetContext')


class GetContext:
    """
    Retrieves the context from different options (Redis, AzureSearch, Milvus, Blob, etc.)
    and returns a DataFrame with the context information.
    Fields: [ID, PID, File Name, Page Number, Text, Similarity Score]
    """

    def __init__(self, data):
        self.query_id = data["query_id"]
        self.context_type = data["context_type"]
        self.filename_column = data["filename_column"]

        # finding context
        if self.context_type == "chroma":
            self.search_connector = data["search_connector"]
        else:
            logger.info(f"[{self.query_id}] [CMap] Unknown context {self.context_type}")

    def get_filename_from_df(self, df):
        """
        Retrieves file names from a DataFrame.

        Args:
            df (pandas.DataFrame): A DataFrame containing file names.

        Returns:
            list: A list of file names extracted from the DataFrame.

        Example:
            cmap = GetContext()
            file_names = cmap.get_filename_from_df(df)
        """
        logger.info(f"[{self.query_id}] [CMap] Inside get_filename_from_df()")
        fnames = []
        try:
            fnames = list(set(list(df[self.filename_column].values)))
        except Exception as e:
            logger.error(
                f"[{self.query_id}] [CMap] Error getting file names from context dataframe: {str(e)}"
            )
        logger.info(f"[{self.query_id}] [CMap] Obtained filenames from context dataframe!")
        return fnames

    def get_context(self, data):
        """
        Retrieves the context (file name, page, text, similarity score) from a data dictionary
        and returns a DataFrame with the top K similar results.

        Args:
            data (dict): A dictionary containing the necessary parameters and data.
            top_k (int, optional): The number of top similar results to retrieve. Defaults to 10.

        Returns:
            data dictionary with
                pandas.DataFrame: A DataFrame with the top K similar results,
                    containing mandatory columns: 'file_name', 'page', 'text', 'score'.
        """
        logger.info(f"[{self.query_id}] [CMap] Inside get_context()")

        # set default variables
        data["context_dataframe"] = None
        data["context_dataframes"] = []
        data["context_filenames"] = []
        data["context_found"] = False
        data["context_relevant"] = False
        data["context_max_similarity"] = 0.0

        logger.info(f"[{self.query_id}] [CMap] Param context_type: {str(data['context_type'])}")

        try:
            if data["context_type"] == "chroma":
                # log azure search specific params
                logger.info(
                    f"[{self.query_id}] [CMap] Param top_n_contexts: {str(data['top_n_contexts'])}"
                )

                # explicitly set redis enabled
                self.search_connector.is_enabled = True

                # embedding model
                embedding_model = data["embedding_model"]

                # get query
                if data["is_followup"]:
                    query = data["updated_query"]
                else:
                    query = data["query"]

                # get query vector from chosen embedding model
                query_vector = embedding_model.get_embedding(query,self.query_id)

                # get redis results dataframe
                top_k = int(data["chroma_top_n_contexts"])

                # search similarity
                chroma_df = self.search_connector.search_vectors(data, query_vector, top_k)

                # define azure search params
                # check if dataframe is empty
                if not chroma_df.empty:
                    # dataframe head 1
                    logger.info(f"[{self.query_id}] [CMap] Context dataframe head 1:")
                    logger.info(chroma_df.head(1))

                    # set azure search dataframe
                    data["context_dataframe"] = chroma_df
                    # update context dataframes with azure search dataframe
                    data["context_dataframes"].append(chroma_df)

                    # update list of reference file names with azure search file names
                    data[
                        "context_filenames"
                    ] = self.get_filename_from_df(chroma_df)
                    # identify all file names | structured data + context
                    data["all_file_names"] = [] #data["sdr_file_names"] #FIXME: Check ModelSingleton.py if we are using StructuredDataReader(data) or not
                    if data["context_filenames"]:
                        for fname in data["context_filenames"]:
                            if fname not in data["all_file_names"]:
                                data["all_file_names"].append(fname)
                    else:
                        logger.warn(f"[{self.query_id}] [CMap] No file names found in context!")
                    logger.info(f"[{self.query_id}] [CMap] List of files (structured & context included)")
                    logger.info(f"[{self.query_id}] [CMap] "+str(data["all_file_names"]))
                    # set context enabled flag
                    data["context_found"] = True

                    # El gate de relevancia (umbral de similitud densa) ya se aplica
                    # DENTRO de ChromaConnection.search_vectors: si ningún candidato lo
                    # supera, search_vectors devuelve un DataFrame vacío y ni siquiera
                    # entramos a este bloque. Si llegamos acá, hay al menos un chunk
                    # relevante.
                    data["context_relevant"] = True

        except Exception as e:
            logger.error(f"[{self.query_id}] [CMap] Azure Search Error getting context: {str(e)}")
    
        logger.info(f"[{self.query_id}] [CMap] Context found for GPT: {str(data['context_found'])}")

        if data["context_found"]:
            logger.info(f"[{self.query_id}] [CMap] Context file names: {str(data['context_filenames'])}")
            logger.info(
                f"[{self.query_id}] [CMap] Context dataframes: {str(len(data['context_dataframes']))}"
            )
            logger.info(
                f"[{self.query_id}] [CMap] Context file names: {str(len(data['context_filenames']))}"
            )
            logger.info(f"[{self.query_id}] [CMap] All file names: {str(len(data['all_file_names']))}")
            logger.info(f"[{self.query_id}] [CMap] Context Identification complete!")
        return data