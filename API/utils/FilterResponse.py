#!/usr/bin/env python
# coding: utf-8

import Levenshtein as lev
import re
import logging
from urllib.parse import urlparse

# logging
logger = logging.getLogger('app.FilterResponse')


# To find the levenshtein matching score
def find_levenshtein(response: str, reference: str, query_id : str) -> float:
    """To check the levenshtein similarity of the given strings and return
    the similarity score in percentage.

    Args:
        response (str): answer string from response
        reference (str): invalid response string

    Returns:
        float: percentage similarity of the given input strings.
    """
    logger.info(f"[{query_id}] [FilterResponse] Inside find_levenshtein")
    ratio = 0.0
    try:
        clean_re = re.compile(r"[\"\.\,\']")
        response = re.sub(clean_re, "", response)
        reference = re.sub(clean_re, "", reference)
        ratio = lev.ratio(response.lower(), reference.lower()) * 100
        logger.info(f"[{query_id}] [FilterResponse] levenshtein similarity score :"+str(ratio))
    except Exception as e:
        logger.info(
            f"[{query_id}] [FilterResponse] Error finding levenshtein match score. Incoming Response: {response}, Reference no_ans: {reference}. Error: {str(e)}"
        )
    return ratio

#To clean the response text
def clean_response(response: str, query_id: str) -> str:
    """To clean the response to avoid different formats observed in openai response.

    Args:
        response (str): response string from openai

    Returns:
        str: cleaned response.
    """
    logger.info(f"[{query_id}][FilterResponse] Inside clean_response")
    raw_response = response
    try:
        if(re.match(r'\n*a\d{0,1}\:*\.*', response.lower()) or re.match(r'\n*answer\d{0,1}\:*\.*', response.lower())):
            response = " ".join(response.split(" ")[1:])
        response = re.sub(r"\n*\s*\#{0,3}\n*\s*A\:+", "", response)
        response = response.strip()
        logger.info(f"[{query_id}][FilterResponse] Response cleaned")
        # response = re.sub("\n", "", response).strip()
    except Exception as e:
        logger.error(
            f"[{query_id}][FilterResponse] Error Cleaning response. Incoming Response: {response}. Error: {str(e)}"
        )
        response = raw_response

    return response

def is_invalid_end_character(url, query_id):
    """
    Checks if the end character of a URL is invalid.

    Args:
    url: The URL to check.

    Returns:
    True if the end character is invalid, False otherwise.
    """
    logger.info(f"[{query_id}] [FilterResponse] Inside is_invalid_end_character()")
    end_character_regex = re.compile(r"[\s<>\[\]\(\)\.\:\,\;\{\}|\\%]")
    match = end_character_regex.match(url[-1])
    if match:
        logger.info(f"[{query_id}] [FilterResponse] End character of url is invalid")
        return True
    else:
        logger.info(f"[{query_id}] [FilterResponse] End character of url is valid")
        return False
    
def format_content(url,content,query_id):
    logger.info(
            f"[{query_id}] [FilterResponse] Inside Format Content()"
        )
    try:
        url_extraction = urlparse(url)
        clean_domain = url_extraction.netloc.split('.')
        capitalized_domain = [word.capitalize() for word in clean_domain]
        domain=' '.join(capitalized_domain[:-1])
    except Exception as e:
        logger.info(
            f"[{query_id}] [FilterResponse] Domain extraction for url:{url} failed: {e}"
        )
        domain=url
    try:
        domain_url = f"[{domain}]({url})"
        # Check if the URL is enclosed in parentheses
        formated_content=content
        if f"({url}" in formated_content:
            formated_content = formated_content.replace(f"({url}", url)
        if f"{url})" in formated_content:
            formated_content = formated_content.replace(f"{url})", url)
        if url in formated_content:
            formated_content = formated_content.replace(url, domain_url)
    except Exception as e:
        logger.info(
            f"[{query_id}] [FilterResponse] Domain mapping in content failed: {e}"
        )
        formated_content = content
    return formated_content

def update_urls(content: str, query_id: str) -> str:
    """
    To convert the urls in openai response clickable.

    Args:
        content (str): response string from openai

    Returns:
        str: formated response.
    """
    logger.info(f"[{query_id}] [FilterResponse] Inside update_urls()")
    matches = re.finditer(r"https://|http://", content)
    formated_content = content
    mapped_urls = []
    for match in matches:
        url = ""
        span = match.span()
        end_matches = re.finditer(r"\s+|\t+|\n+", content[span[0]:])
        for e_match in end_matches:
            if e_match:
                e_span = e_match.span()
                url = content[span[0]:][:e_span[0]]
                while (url.startswith(".") or url.startswith("(")):
                    url = url[1:]
                while is_invalid_end_character(url[-1], query_id):
                    url = url[:-1]
                if url not in mapped_urls:
                    formated_content=format_content(url,formated_content,query_id)
                    break
        else:
            url = content[span[0]:]
            while (url.startswith(".") or url.startswith("(")):
                url = url[1:]
            while is_invalid_end_character(url[-1], query_id):
                url = url[:-1]
            if url not in mapped_urls:
                formated_content=format_content(url,formated_content,query_id)
                #formated_content = formated_content.replace(url, '<a target="_blank" href="'+url.strip()+'">'+url.strip()+"</a>")
        mapped_urls.append(url)

    logger.info(
                    f"[{query_id}] [FilterResponse] update_urls updated answer: {formated_content}"
        )
    return formated_content

def verify_response(results: list, query_id: str) -> list:
    """To varify whether the response answer matches with any invalid response case
    and assign a flag "is_valid" to True if Valid response else False.

    Args:
        results (list): response list from pai/rag/gpt

    Returns:
        list: response list with a "is_valid" flag
    """
    logger.info(f"[{query_id}] [FilterResponse] Inside verify_response()")
    invalid_answers = ["I don't know.", "None"]
    for res in results:
        for no_ans in invalid_answers:
            if (not res["answer"].strip()):
                res["is_valid"] = False
                break
            elif (find_levenshtein(clean_response(str(res['answer']), query_id).lower(), no_ans.lower(), query_id) >= 60):
                res["answer"] = clean_response(str(res['answer']),query_id)
                res["is_valid"] = False
                break
        else:
            #res['answer'] = update_urls(str(res['answer']), query_id)
            res["is_valid"] = True
    logger.info(f"[{query_id}][FilterResponse] Completed verify_response()")
    return results


def url_formatting(results: list, query_id: str) -> list:
    """To format the urls to make it clickable for valid responses.

    Args:
        results (list): response list from gpt

    Returns:
        list: response list with a formatted urls in the answer content
    """
    logger.info(f"[{query_id}][FilterResponse] Inside url_formatting()")
    for res in results:
        if res["is_valid"]:
            res['answer'] = update_urls(str(res['answer']),query_id)
    return results


def filter_response(results: list, query_id: str) -> list:
    """To filter out the invalid response and only return vaild responses. In case if all the results are
    invalid, just return only one invalid case and disable the citation by assigning page and score to 0.

    Args:
        results (list): list response dicts from pai/rag/gpt

    Returns:
        list: list valid respopnses after filtering
    """
    logger.info(f"[{query_id}][FilterResponse] Inside filter_response()")
    try:
        filtered_response = []
        results_verified = verify_response(results, query_id)
        filtered_response.extend([res for res in results_verified if res["is_valid"]])

        if (len(filtered_response) == 0):
            filtered_response.append(results_verified[0])
            filtered_response[0]["file_name"] = ""
            filtered_response[0]["page"] = 0
            filtered_response[0]["similarity_score"] = 0.0
            return filtered_response
        else:
            return filtered_response
    except Exception as e:
        logger.info(
            f"[{query_id}][FilterResponse] Error in Filtering response. Results: {results}. Error: {str(e)}"
        )
        return results
