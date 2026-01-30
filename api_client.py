#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
API客户端 - 搜题服务接口

功能：
- 向外部搜题API发送请求获取答案
- 支持重试机制和错误处理
- 统计请求成功率

配置：
- 端点: http://8.155.30.94:5000/api/get_answer
- 密钥: bot666

作者：AI Assistant
日期：2026-01-30
"""

import requests
import json
import time


class APIClient:
    """
    搜题API客户端

    用于向外部搜题服务发送请求，获取题目答案
    """

    def __init__(self, api_key="bot666"):
        """
        初始化API客户端

        Args:
            api_key: API访问密钥，默认bot666
        """
        self.api_key = api_key
        self.timeout = 20  # 请求超时时间（秒）
        self.retry_count = 3  # 重试次数
        self.request_count = 0  # 请求总数
        self.success_count = 0  # 成功次数

    def search_answer(
        self, question_text, options=None, course_name=None, chapter=None
    ):
        """
        搜索题目答案

        向API发送题目信息，获取答案

        Args:
            question_text: 题目文本（必填）
            options: 选项列表，可选
            course_name: 课程名称，可选
            chapter: 章节名称，可选

        Returns:
            str: 答案选项（如"A"、"B"），失败返回None
        """
        for attempt in range(self.retry_count):
            try:
                # 构建请求数据
                data = {
                    "questionId": f"q_{int(time.time())}",  # 唯一ID
                    "title": question_text,
                    "isMultiple": False,
                    "options": options or [],
                    "visibilityScore": "",
                    "courseInfo": {
                        "courseName": course_name or "",
                        "chapter": chapter or "",
                        "fullText": "",
                    }
                    if course_name or chapter
                    else None,
                }

                # 发送POST请求
                response = requests.post(
                    "http://8.155.30.94:5000/api/get_answer",
                    json=data,
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                        "x-api-key": self.api_key,
                    },
                    timeout=self.timeout,
                )

                self.request_count += 1

                # ========== 响应处理 ==========
                if response.status_code == 200:
                    try:
                        result = response.json()

                        # 解析多种响应格式
                        if isinstance(result, dict):
                            # 格式1: {"success": true, "data": {"correctAnswer": "A"}}
                            if result.get("success") and "data" in result:
                                data_obj = result["data"]
                                if (
                                    isinstance(data_obj, dict)
                                    and "correctAnswer" in data_obj
                                ):
                                    answer = data_obj["correctAnswer"]
                                    self.success_count += 1
                                    return answer

                            # 格式2: {"answer": "A"}
                            if "answer" in result:
                                answer = result["answer"]
                                self.success_count += 1
                                return answer

                            # 格式3: {"result": "A"}
                            if "result" in result:
                                answer = result["result"]
                                self.success_count += 1
                                return answer

                        return None

                    except json.JSONDecodeError:
                        if attempt < self.retry_count - 1:
                            time.sleep(1)
                            continue
                        return None

                # ========== HTTP错误处理 ==========
                elif response.status_code == 404:
                    print(f"  [API] 404 - 端点不存在")
                    if attempt < self.retry_count - 1:
                        print(f"  [API] 重试 {attempt + 1}/{self.retry_count}...")
                        time.sleep(2)
                        continue
                    return "NOT_FOUND"

                elif response.status_code == 401:
                    print(f"  [API] 401 - 密钥无效")
                    return "UNAUTHORIZED"

                elif response.status_code == 500:
                    print(f"  [API] 500 - 服务器错误")
                    if attempt < self.retry_count - 1:
                        time.sleep(2)
                        continue
                    return None

                else:
                    print(f"  [API] HTTP {response.status_code}")
                    if attempt < self.retry_count - 1:
                        time.sleep(1)
                        continue
                    return None

            # ========== 网络异常处理 ==========
            except requests.exceptions.Timeout:
                print(f"  [API] 超时")
                if attempt < self.retry_count - 1:
                    time.sleep(1)
                    continue
                return None

            except requests.exceptions.ConnectionError as e:
                print(f"  [API] 连接失败")
                if attempt < self.retry_count - 1:
                    time.sleep(1)
                    continue
                return None

            except Exception as e:
                print(f"  [API] 错误: {e}")
                if attempt < self.retry_count - 1:
                    time.sleep(1)
                    continue
                return None

        return None

    def test_connection(self):
        """
        测试API连接

        发送测试请求验证API是否可用

        Returns:
            bool: 连接是否成功
        """
        print("=" * 60)
        print(" API 连接测试")
        print("=" * 60)

        print(f"端点: http://8.155.30.94:5000/api/get_answer")
        print(f"密钥: {self.api_key}")

        # 测试题目
        test_question = "马克思主义的内在品质是（  ）"
        test_options = [
            {"value": "A", "text": " 人民性"},
            {"value": "B", "text": " 实践性"},
            {"value": "C", "text": " 发展性"},
            {"value": "D", "text": " 革命性"},
        ]

        print(f"\n测试题目: {test_question}")

        answer = self.search_answer(
            test_question, options=test_options, course_name="测试", chapter="测试"
        )

        print("\n" + "=" * 60)
        if answer and answer not in ["NOT_FOUND", "UNAUTHORIZED"]:
            print(f"[成功] API连接正常")
            print(f"[答案] {answer}")
            return True
        else:
            print(f"[失败] API连接异常")
            if answer == "NOT_FOUND":
                print("[原因] 端点不存在 (404)")
            elif answer == "UNAUTHORIZED":
                print("[原因] 密钥无效 (401)")
            return False

    def get_stats(self):
        """
        获取请求统计信息

        Returns:
            dict: 包含请求数、成功数、成功率
        """
        return {
            "requests": self.request_count,
            "success": self.success_count,
            "rate": f"{(self.success_count / max(self.request_count, 1) * 100):.1f}%",
        }


if __name__ == "__main__":
    client = APIClient(api_key="bot666")
    success = client.test_connection()

    stats = client.get_stats()
    print(
        f"\n统计: 请求{stats['requests']}次, 成功{stats['success']}次, 成功率{stats['rate']}"
    )
