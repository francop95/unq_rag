import pandas as pd
import logging

# logging
logger = logging.getLogger('app.parser')

def parse_conv_history(conv_history_dict, query_id):
    logger.info(f"[{query_id}] [Utils] Inside parse_conv_history")
    parsed_df = pd.DataFrame(
        columns=["question", "answer", "file_name", "page_num", "user_like"]
    )
    if not conv_history_dict:
        return parsed_df
    try:
        for i, prev_question in enumerate(conv_history_dict):
            answers = []
            file_names = []
            page_nums = []
            user_like = []
            for ans in prev_question["answers"]:
                answers.append(ans["answer"])
                if not ans["files"]:
                    file_names.append("")
                    page_nums.append(0)
                else:
                    file_names.append(ans["files"][0]["fileName"])
                    page_nums.append(ans["files"][0]["pages"][0]["pageNumber"])
                user_like.append(ans["like"])
            parsed_df.loc[i] = [
                prev_question["question"],
                answers,
                file_names,
                page_nums,
                user_like,
            ]
            logger.info(f"[{query_id}] [Utils] Parsed conversation history")
    except Exception as e:
        logger.info(
            "[{}][Utils] parse_conv_history failure, Error: {}, conv_history_input: {}".format(
                query_id, e, conv_history_dict
            )
        )
    return parsed_df
