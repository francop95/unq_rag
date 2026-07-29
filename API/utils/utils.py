import logging
import tiktoken


logger = logging.getLogger('app.utils')

# To avoid checkmarx issue
def sanitize_data(data):
    """
    Sanitize the data (dictionary) to prevent XSS attacks.
    """
    if isinstance(data, dict):
        # If the input is a dictionary, recursively sanitize each value
        return {key: sanitize_data(value) for key, value in data.items()}
    elif isinstance(data, str):
        data = data.replace("&", "&amp;") # Must be done first!
        data = data.replace("<", "&lt;")
        data = data.replace(">", "&gt;")
        data = data.replace('"', "&quot;")
        # If the input is a string, sanitize it
        return data
    else:
        # For other data types, return as is
        return data
    

def calculate_num_of_tokens(text, query_id, encoding="cl100k_base"):
    try:
        logger.info(f"[{query_id}] [Utils] Calculating number of tokens ")
        encoding = tiktoken.get_encoding("cl100k_base")
        return len(encoding.encode(text))
    except Exception as e:
        logger.error(f"[{query_id}] [Utils] Exception in calculating tokens : {str(e)}")
        return 0    
