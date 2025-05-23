# encoding:utf-8

import time
import requests

from bot.bot import Bot
from bot.session_manager import SessionManager
from bridge.context import ContextType
from bridge.reply import Reply, ReplyType
from common.log import logger
from config import conf
from .hunyuan_session import HunyuanSession


# 腾讯混元AI对话模型API
class HunyuanBot(Bot):
    def __init__(self):
        super().__init__()
        self.sessions = SessionManager(HunyuanSession, model=conf().get("model") or "hunyuan-turbos-latest")
        model = conf().get("model") or "hunyuan-turbos-latest"
        self.args = {
            "model": model,  # 对话模型的名称
            "temperature": conf().get("temperature", 0.7),  # 温度参数
            "top_p": conf().get("top_p", 0.95),  # 使用默认值
        }
        self.api_key = conf().get("open_ai_api_key")
        self.base_url = conf().get("open_ai_api_base", "https://api.hunyuan.cloud.tencent.com/v1/chat/completions")

    def reply(self, query, context=None):
        # acquire reply content
        if context.type == ContextType.TEXT:
            logger.info("[HUNYUAN_AI] query={}".format(query))

            session_id = context["session_id"]
            reply = None
            clear_memory_commands = conf().get("clear_memory_commands", ["#清除记忆"])
            if query in clear_memory_commands:
                self.sessions.clear_session(session_id)
                reply = Reply(ReplyType.INFO, "记忆已清除")
            elif query == "#清除所有":
                self.sessions.clear_all_session()
                reply = Reply(ReplyType.INFO, "所有人记忆已清除")
            if reply:
                return reply
            session = self.sessions.session_query(query, session_id)
            logger.debug("[HUNYUAN_AI] session query={}".format(session.messages))

            model = context.get("hunyuan_model")
            new_args = self.args.copy()
            if model:
                new_args["model"] = model

            reply_content = self.reply_text(session, args=new_args)
            logger.debug(
                "[HUNYUAN_AI] new_query={}, session_id={}, reply_cont={}, completion_tokens={}".format(
                    session.messages,
                    session_id,
                    reply_content["content"],
                    reply_content["completion_tokens"],
                )
            )
            if reply_content["completion_tokens"] == 0 and len(reply_content["content"]) > 0:
                reply = Reply(ReplyType.ERROR, reply_content["content"])
            elif reply_content["completion_tokens"] > 0:
                self.sessions.session_reply(reply_content["content"], session_id, reply_content["total_tokens"])
                reply = Reply(ReplyType.TEXT, reply_content["content"])
            else:
                reply = Reply(ReplyType.ERROR, reply_content["content"])
                logger.debug("[HUNYUAN_AI] reply {} used 0 tokens.".format(reply_content))
            return reply
        else:
            reply = Reply(ReplyType.ERROR, "Bot不支持处理{}类型的消息".format(context.type))
            return reply

    def reply_text(self, session: HunyuanSession, args=None, retry_count=0) -> dict:
        """
        call hunyuan api to get the answer
        :param session: a conversation session
        :param args: additional args
        :param retry_count: retry count
        :return: {}
        """
        try:
            headers = {
                "Content-Type": "application/json",
                "Authorization": "Bearer " + self.api_key
            }
            body = args or self.args.copy()
            body["messages"] = session.messages

            res = requests.post(
                self.base_url,
                headers=headers,
                json=body,
                timeout=conf().get("request_timeout", 180)
            )
            if res.status_code == 200:
                response = res.json()
                return {
                    "total_tokens": response["usage"]["total_tokens"],
                    "completion_tokens": response["usage"]["completion_tokens"],
                    "content": response["choices"][0]["message"]["content"]
                }
            else:
                response = res.json()
                error = response.get("error")
                logger.error(f"[HUNYUAN_AI] chat failed, status_code={res.status_code}, "
                             f"msg={error.get('message')}, type={error.get('type')}")

                result = {"completion_tokens": 0, "content": "提问太快啦，请休息一下再问我吧"}
                need_retry = False
                if res.status_code >= 500:
                    # server error, need retry
                    logger.warn(f"[HUNYUAN_AI] do retry, times={retry_count}")
                    need_retry = retry_count < 2
                elif res.status_code == 401:
                    result["content"] = "授权失败，请检查API Key是否正确"
                elif res.status_code == 429:
                    result["content"] = "请求过于频繁，请稍后再试"
                    need_retry = retry_count < 2
                else:
                    need_retry = False

                if need_retry:
                    time.sleep(3)
                    return self.reply_text(session, args, retry_count + 1)
                else:
                    return result
        except Exception as e:
            logger.exception(e)
            need_retry = retry_count < 2
            result = {"completion_tokens": 0, "content": "我现在有点累了，等会再来吧"}
            if need_retry:
                return self.reply_text(session, args, retry_count + 1)
            else:
                return result