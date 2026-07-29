#!/usr/bin/env python
# coding: utf-8

import Levenshtein as lev
import pandas as pd
import re



# To find the levenshtein matching score
def find_levenshtein(response, reference):
    clean_re = re.compile(r"[\"\.\,\']")
    response = re.sub(clean_re, "", response)
    reference = re.sub(clean_re, "", reference)
    ratio = lev.ratio(response.lower(), reference.lower()) * 100
    return ratio

#To clean the response text
def clean_response(response: str) -> str:
    """To clean the response to avoid different formats observed in openai response.

    Args:
        response (str): response string from openai

    Returns:
        str: cleaned response.
    """
    if(re.match(r'\n*a\d{0,1}\:*\.*', response.lower()) or re.match(r'\n*answer\d{0,1}\:*\.*', response.lower())):
        response = " ".join(response.split(" ")[1:])
    response = re.sub(r"\n*\s*\#{0,3}\n*\s*A\:+", "", response)
    response = response.strip()
    # response = re.sub("\n", "", response).strip()
    return response

def verify_response(results: list) -> list:
    results_filtered = []
    invalid_answers = ["I don't know.", "None"]
    invalid_res_in_flag = False
    for res in results:
        invalid_res_match = False
        for no_ans in invalid_answers:
            if (not res["answer"].strip()):
                res["is_valid"] = False
                break                
            elif (find_levenshtein(clean_response(res['answer']).lower(), no_ans.lower()) >= 60):
                res["is_valid"] = False
                break
            res["is_valid"] = True
    return results
def get_ans_by_type(results_verified, ans_type):
    answers = []
    for res in results_verified:
        if (res["ans_type"] == ans_type):
            answers.append(res)
    return answers



def filter_response(results, data):
    filtered_response = []
    results_verified = verify_response(results)
    rag_answers = get_ans_by_type(results_verified, "rag")
    gpt_answers = get_ans_by_type(results_verified, "gpt")
    pai_answers = get_ans_by_type(results_verified, "pai")
    
    #pai + gpt + rag
    if ((data["pandas_ai_enabled"] or data["is_gpt_enabled"]) and data["is_rag_enabled"]):
        if data["rag_ans_found"]:
            if (rag_answers[0]["is_valid"]):
                filtered_response.append(rag_answers[0])
                return filtered_response
        if data["gpt_ans_found"]:
            for gpt_res in gpt_answers:
                if gpt_res["is_valid"]:
                    filtered_response.append(gpt_res)      
            if (len(filtered_response) == 0):
                gpt_res = gpt_answers[0]
                gpt_res["page"] = 0
                gpt_res["similarity_score"] = 0
                filtered_response.append(gpt_res)
            return filtered_response
        if (data["pai_ans_found"] and pai_answers[0]["is_valid"]):
            filtered_response.append(pai_answers[0])
            return filtered_response
        else:
            gpt_res = gpt_answers[0]
            gpt_res["page"] = 0
            gpt_res["similarity_score"] = 0
            filtered_response.append(gpt_res)
        return filtered_response
    # pai + gpt
    elif (data["pandas_ai_enabled"] and data["is_gpt_enabled"]):
        if data["gpt_ans_found"]:
            for gpt_res in gpt_answers:
                if gpt_res["is_valid"]:
                    filtered_response.append(gpt_res)
                    
        if (data["pai_ans_found"] and pai_answers[0]["is_valid"]):
            filtered_response.append(pai_answers[0])
        if (len(filtered_response) == 0):
            gpt_res = gpt_answers[0]
            gpt_res["page"] = 0
            gpt_res["similarity_score"] = 0
            filtered_response.append(gpt_res)
        return filtered_response
    
    # gpt
    elif (data["is_gpt_enabled"]):
        if data["gpt_ans_found"]:
            for gpt_res in gpt_answers:
                if gpt_res["is_valid"]:
                    filtered_response.append(gpt_res)
        if (len(filtered_response) == 0):
            gpt_res = gpt_answers[0]
            gpt_res["page"] = 0
            gpt_res["similarity_score"] = 0
            filtered_response.append(gpt_res)
        return filtered_response
    
    #pai
    elif data["pandas_ai_enabled"]:
        if pai_answers[0]["is_valid"]:
            filtered_response.append(pai_answers[0])
        else:
            pai_answers[0]["page"] = 0
            pai_answers[0]["similarity_score"] = 0
            filtered_response.append(pai_answers[0])
        return filtered_response      

