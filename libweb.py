#!/bin/python
# coding=utf-8
# 2024-5-14
# 由于目前本地使用的一些大模型并不具备online的能力，现在我们希望通过外挂一个搜索引擎实现对这种online能力的支持
# 目前，经过测试，百度搜索网页版提供的功能已经足够我们使用。
# 2026-3-5 
# Add new function: trace 303 redirect page and get final page(supported by curl in the system)


import requests
import datetime
from bs4 import BeautifulSoup
import json
from http import HTTPStatus
#import dashscope
#dashscope.api_key = "sk-82b3bfa35dce4e93877d7a54841d157d"
import os, sys


proxies={
    'http' : 'http://127.0.0.1:7890',
    'https': 'http://127.0.0.1:7890'
}

headers = {
    'Content-Type': 'application/json',
    'Accept'    : 'application/json',
    'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0'
}


def check_curl(): # check if system installed curl. If curl is installed, we use curl instead of python requests.
    if(sys.platform == 'win32'):command = 'where curl'
    else:                       command = 'which curl'
    try:
        result = os.popen(command).read()
        if result.strip(): return True
        else: return False
    except Exception:
        return False


def extract_page_with_curl(url):
    localtime = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"temp_web_page_{localtime}.html"
    command = f'curl -k -L -s "{url}" -o "{filename}"'
    # extract page
    os.system(command)
    try:
        with open(filename,'r',encoding='utf-8') as f:
            txt = f.read()
    except Exception as e:
        print(f"Some thing wrong with curl. message: {e}")
        txt = ""
    # clean temp file
    if(sys.platform == 'win32'):command = f'del {filename}'
    else:                       command = f'rm  {filename}'
    os.system(command)
    # parse html file
    if(len(txt)>0):
        bs4obj = BeautifulSoup(txt,'lxml')
        page_content = bs4obj.get_text().strip().replace("\n\n","\n").replace("\n\n","\n").replace("\n\n","\n")
    else:
        page_content = ""
    return page_content


def extract_page_with_requests(url):
    try:   req = requests.get(url,headers=headers)
    except:req = requests.get(url,headers=headers,proxies=proxies)
    req.encoding = "utf-8"
    txt = req.text
    bs4obj = BeautifulSoup(txt,'lxml')
    page_content = bs4obj.get_text().strip().replace("\n\n","\n").replace("\n\n","\n").replace("\n\n","\n")
    return page_content

def run(url):
    if(check_curl):return extract_page_with_curl(url)
    else:          return extract_page_with_requests(url)

if(__name__=="__main__"):
    if(len(sys.argv)<2):
        print("Usage: \n\tpython extract_webpage_with_redirect.py [URL]")
        sys.exit(0)
    url = sys.argv[1]
    print(run(url))




