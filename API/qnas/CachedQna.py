# load libraries
import logging
from utils.FilterResponse import find_levenshtein
from typing import Dict, Any

# logging
logger = logging.getLogger('app.GetCacheQna')

class GetCacheQna:
    """
    Retrieves the cached Question and answer from different options (Azure Search, Redis, Milvus, Blob, etc.)
    and returns a DataFrame with the context information.
    Fields: [ID, PID, Question, Answer, File Name, Page Number, Answer Type, Similarity Score]
    """

    def __init__(self, data):
        self.query_id = data["query_id"]
        self.azure_search_connector = CacheAzureSearchConnection(data)
        logger.info(f"[{self.query_id}] [CachedQna] Initialization complete")

    def get_cache_qna(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Retrieves the similar cached Question and answer and associated metadata (question, answer, file name, page, ans_type, similarity score) from a data dictionary
        and returns a DataFrame with the top K similar results.

        Args:
            data (dict): A dictionary containing the necessary parameters and data.
            top_k (int, optional): The number of top similar results to retrieve. Defaults to 5.

        Returns:
            data dictionary with
                pandas.DataFrame: A DataFrame with the top K similar results,
                    containing mandatory columns: 'question', 'answer', 'file_name', 'page', 'ans_type', 'score'.
        """
        logger.info(f"[{self.query_id}] [CachedQna] Inside get_cache_qna()")
        data["cache_qna_response"] = []
        # set cache Qna found flag
        data["cache_found"] = False
        try:
            # log specific params
            logger.info(
                f"[{self.query_id}] [CachedQna] Param cache_top_n: {str(data['cache_top_n'])}"
            )

            # explicitly set azure search connector enabled
            self.azure_search_connector.is_enabled = True

            # embedding model
            embedding_model = data["embedding_model"]

            # get query
            query = data["query"]

            # Check if the same query is raised just before
            data = self.check_prev_question(data)
            if data["prev_question_match"]:
                return data

            # get query vector from chosen embedding model
            query_vector = embedding_model.get_embedding(query,self.query_id) #Fix Me GPO not sending query id

            # get results dataframe
            top_k = int(data["cache_top_n"])

            # search similarity
            result_df = self.azure_search_connector.search_vectors(data, query_vector, top_k)

            # define params
            # check if dataframe is empty

            if not result_df.empty:
                # dataframe head 1
                logger.info(f"[{self.query_id}] [CachedQna] Cache QnA dataframe: {result_df}")

                data["cache_qna_response"] = result_df.to_dict(orient="records")

                # set cache Qna found flag
                data["cache_found"] = True

        except Exception as e:
            logger.error(f"[{self.query_id}] [CachedQna] Error getting Cached Qna: {str(e)}")

        logger.info(f"[{self.query_id}] [CachedQna] Cache Qna search complete!")
        return data

    def check_prev_question(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        To check whther the query is an exact match for the previous question and whether user
        disliked the cache answer, if the previous

        Args:
            data (Dict[str, Any]): _description_

        Returns:
            Dict[str, Any]: returns prev_question_match as True in the data dict.
        """
        logger.info(f"[{self.query_id}] [CachedQna]Inside check_prev_question()")
        prev_question_df = data["conv_history_df"].head(1)
        data["prev_question_match"] = False
        if ((find_levenshtein(data["query"], str(prev_question_df["question"]), data['query_id']) >= data["min_cache_similarity"]*100) and
                (not prev_question_df["user_like"])):
            data["prev_question_match"] = True
        return data
