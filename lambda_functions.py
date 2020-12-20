import json
import jwt
import requests
import urllib
import boto3
import os
from datetime import datetime, timezone, timedelta


from pybacklogpy.BacklogConfigure import BacklogJpConfigure
from pybacklogpy.Issue import Issue

ssm = boto3.client('ssm')

JST = timezone(timedelta(hours=+9), 'JST')


####################################
# Systems Manager パラメータストア #
####################################
def get_parameter(key):
    """
    SSMパラメータストアからパラメータ取得
    """
    response = ssm.get_parameters(
        Names=[
            key
        ],
        WithDecryption=True
    )
    parameters = response["Parameters"]
    if len(parameters) > 0:
        return response['Parameters'][0]["Value"]
    else:
        return ""


def put_parameter(key, value):
    """
    SSMパラメータストアへパラメータを格納
    """
    ssm.put_parameter(
        Name=key,
        Value=value,
        Type='SecureString',
        Overwrite=True
    )


##################
# LINE WORKS API #
##################
def get_jwt(server_list_id, server_list_privatekey):
    """
    LINE WORKS アクセストークンのためのJWT取得
    """
    current_time = datetime.now().timestamp()
    iss = server_list_id
    iat = current_time
    exp = current_time + (60 * 60) # 1時間

    secret = server_list_privatekey

    jwstoken = jwt.encode(
        {
            "iss": iss,
            "iat": iat,
            "exp": exp
        }, secret, algorithm="RS256")

    return jwstoken.decode('utf-8')


def get_server_token(api_id, jwttoken):
    """
    LINE WORKS アクセストークン取得
    """
    url = 'https://authapi.worksmobile.com/b/{}/server/token'.format(api_id)

    headers = {
        'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8'
    }

    params = {
        "grant_type": urllib.parse.quote("urn:ietf:params:oauth:grant-type:jwt-bearer"),
        "assertion": jwttoken
    }

    form_data = params

    r = requests.post(url=url, data=form_data, headers=headers)

    body = json.loads(r.text)
    access_token = body["access_token"]

    return access_token


def send_message(content, api_id, botno, consumer_key, access_token, account_id):
    """
    LINE WORKS メッセージ送信
    """
    url = 'https://apis.worksmobile.com/r/{}/message/v1/bot/{}/message/push'.format(api_id, botno)

    headers = {
          'Content-Type': 'application/json;charset=UTF-8',
          'consumerKey': consumer_key,
          'Authorization': "Bearer " + access_token
        }

    params = {
            "accountId": account_id,
            "content": content
        }

    form_data = json.dumps(params)

    r = requests.post(url=url, data=form_data, headers=headers)

    return r


######################
# Lambda関数ハンドラ #
######################
def update_token_handler(event, context):
    """
    LINE WORKS アクセストークン定期更新 Lambdaハンドラー関数
    """
    # SSMパラメータストアからLINE WORKSのパラメータを取得
    api_id = get_parameter("lw_api_id")
    server_list_id = get_parameter("lw_server_list_id")
    server_list_privatekey = \
        get_parameter("lw_server_list_private_key").replace("\\n", "\n")
    # JWT取得
    jwttoken = get_jwt(server_list_id, server_list_privatekey)

    # Server token取得
    access_token = get_server_token(api_id, jwttoken)

    # Access Tokenをパラメータストアに設定
    put_parameter("lw_access_token", access_token)

    return


def push_todays_backlog_issues(event, context):
    """
    今日の課題を通知
    """
    botno = os.environ.get("LW_BOTNO")
    account_id = os.environ.get("LW_ACCOUNT_ID")

    # SSMパラメータストアからLINE WORKSのパラメータを取得
    api_id = get_parameter("lw_api_id")
    access_token = get_parameter("lw_access_token")
    consumer_key = get_parameter("lw_server_api_consumer_key")
    backlog_api_key = get_parameter("backlog_api_key")

    # Backlog
    # Backlogのパラメータ
    backlog_user_id = os.environ.get("BL_USER_ID")
    backlog_space_key = get_parameter("backlog_space_key")
    backlog_api_key = get_parameter("backlog_api_key")
    # Backlog設定読み込み
    config = BacklogJpConfigure(space_key=backlog_space_key,
                                api_key=backlog_api_key)
    issue_api = Issue(config)

    # 現在日付
    today = datetime.now(JST).date().strftime('%Y-%m-%d')
    print(today)
    # 担当課題一覧取得
    response = issue_api.get_issue_list(
        start_date_until=today,
        assignee_id=[backlog_user_id],
        status_id=[1, 2, 3],
        count=100
    )
    issues = response.json()

    num = 0
    length = len(issues)
    elements = []
    while num < length:
        i = issues[num]
        print("{} {}: {} : {} : start {} end {}".format(
                i["issueKey"],
                i["summary"],
                i["priority"]["name"],
                i["status"]["name"],
                i["startDate"],
                i["dueDate"]
            )
        )
        element = {
            "title": "{} {}".format(i["issueKey"], i["summary"]),
            "subtitle": "期限: {}".format(i["dueDate"])
        }
        elements.append(element)

        num += 1
        if num % 4 == 0:
            # 4件ずつ 送信
            res_content = {
                "type": "list_template",
                "elements": elements
            }
            r = send_message(res_content, api_id, botno, consumer_key, access_token, account_id)
            print(r.text)
            elements = []

    return
