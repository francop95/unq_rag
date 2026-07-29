import logging
import tiktoken
import pandas as pd
from configs.Configuration import Configuration


logger = logging.getLogger('app.utils')

# Fetching details from config
config = Configuration()

def calculate_cost(model,total_prompt_token,total_completion_token, query_id):
    ## cost = tokens * cost for each token multiplication
    logger.info(f"[{query_id}] [Utils] Inside calculate_cost")
    cost_model = config.get('models_cost')
    if model in cost_model:
        prompt_cost_1000_token = cost_model[model]['cost_per_token_prompt']
        completion_cost_1000_token = cost_model[model]['cost_per_token_completion']

    cost_prompt = total_prompt_token * (prompt_cost_1000_token / 1000)
    cost_completion = total_completion_token * (completion_cost_1000_token / 1000)
    cost = cost_prompt + cost_completion
    logger.info(f"[{query_id}] [Utils] Calculated cost : "+str(cost))
    return cost

def calculate_token_cost(data):
    logger.info(f"[{data['query_id']}] [Utils] Inside calculate_token_cost")
    final_cost=0
    for key,value in data.items():
        if "OAI_raw_response_" in key:
            for v in value:
                total_prompt_token = v['usage']['prompt_tokens']
                total_completion_token = v['usage']['completion_tokens']# ##value ## this value will contain token count and other information needed for cost calculation
                model=v['model']
                cost = calculate_cost(model,total_prompt_token,total_completion_token)
                final_cost = final_cost+cost
    data['cost'] = final_cost
    logger.info(f"[{data['query_id']}] [Utils] Calculated token cost : "+str(final_cost))
    return final_cost

def convert_to_json(data):
    logger.info(f"[{data['query_id']}] [Utils] Inside convert_to_json")
    data_copy = data.copy()
    for key, value in data_copy.items():
        if isinstance(value, pd.DataFrame):
            data_copy[key] = [value.to_json(orient='records')]
        elif isinstance(value, list):
            df_json_list = []
            for item in value:
                if isinstance(item, pd.DataFrame):
                    df_json_list.append(item.to_json(orient='records'))
                else:
                    df_json_list.append(item)

            data_copy[key] = df_json_list
    logger.info(f"[{data['query_id']}] [Utils] Data dict json conversion done")
    return data_copy

def generate_metrics_data(data,query_id):
    logger.info(f"[{query_id}] [Utils] Inside generate_metrics_data")

    metrics_dict = {}

    try:
        metrics_dict["actual_query"] = data.get("query", "")
        metrics_dict["rephrased_query"] = data.get("updated_query", "")
        metrics_dict["cache_answer"] = data.get("cache_found", "")
        metrics_dict["gpt_answer"] = data.get("gpt_ans_found", "")

        metrics_dict["query_intent_type"] = data.get("query_intent", {}).get("question_type", "")
        metrics_dict["query_intent_response"] = data.get("query_intent", {}).get("response", "")
        
        metrics_dict["openai_calls"] = {"query_intent": data.get("query_intent_raw_gpt_response", []),
                                        "custom_retriever": data.get("retriver_raw_gpt_response",[]),
                                        "retriever_qna": data.get("retriver_qna_raw_gpt_response",[])}

        metrics_dict["time_taken"] = {"query_intent": data.get("query_intent_raw_gpt_response", [{}])[0].get("time_taken", 0),
                                      "custom_retriever": data.get("retriver_raw_gpt_response", [{}])[0].get("time_taken", 0),
                                      "retriever_qna": data.get("retriver_qna_raw_gpt_response", [{}])[0].get("time_taken", 0)}
        
        metrics_dict["response_code"] = {"query_intent": data.get("query_intent_raw", [{}])[0].get("res_status", ""),
                                         "custom_retriever": data.get("retriever_response", [{}])[0].get("res_status", ""),
                                         "retriever_qna": data.get("gpt_response_raw", [{}])[0].get("res_status", "")}
        
        metrics_dict["similar_context_ids"] = data["data_df"][["file_name","page_num","global"]].to_dict('records')
        metrics_dict["relevant_context_ids"] =data["retriever_out_df"][["file_name","page_num","global"]].to_dict('records')

        logger.info(f"[{query_id}] [Utils] Metrics data generated")

    except Exception as e:
        logger.error(f"[{query_id}] [Utils] Exception in generating metrics data: {str(e)}")
    
    return metrics_dict

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
