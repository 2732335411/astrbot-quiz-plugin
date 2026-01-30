#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
智能答题系统 - 完整修复版

功能：
1. 自动登录三三制平台
2. 获取课程和章节列表
3. 查询已完成答题的章节（从各课程成绩页获取真实成绩）
4. 智能答题：优先本地题库，缺失时API搜题

成绩查询逻辑：
- 访问 /index/exam/exam_list/course_id/{course_id}.html 获取课程成绩
- 页面显示该课程所有章节的完成状态和分数
- 有分数/成绩的章节 = 已完成

作者：AI Assistant
日期：2026-01-30
"""

import json
import re
import time
import sys
from pathlib import Path
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from auto_login import SanSanZhiAutoLogin


class QuizBot(SanSanZhiAutoLogin):
    """
    智能答题机器人

    继承自SanSanZhiAutoLogin，拥有登录能力和答题功能
    """

    def __init__(
        self,
        api_key="bot666",
        username=None,
        password=None,
        api_endpoint=None,
        question_bank_path=None,
    ):
        """
        初始化答题机器人

        Args:
            api_key: API密钥，用于调用搜题服务
            username: 平台用户名（可选）
            password: 平台密码（可选）
            api_endpoint: API端点（可选）
            question_bank_path: 本地题库路径（可选）
        """
        super().__init__(username=username, password=password)

        # ========== 题库配置 ==========
        self.question_bank = {}  # 本地题库数据
        self.target_courses = [  # 目标课程（有本地题库的课程）
            "马克思主义基本原理",
            "中华民族共同体概论",
            "习近平教育重要论述要",
        ]

        # ========== API配置 ==========
        self.api_key = api_key
        self.api_endpoint = (
            api_endpoint or "http://8.155.30.94:5000/api/get_answer"
        )
        self.api_available = True  # API是否可用
        self.api_error_count = 0  # API错误计数，超过3次则禁用

        # ========== 已完成章节缓存 ==========
        self.completed_chapters = {}  # 已完成章节字典 {exam_id: 成绩信息}
        self.completed_chapters_loaded = False  # 是否已加载完成状态

        # ========== 题库路径 ==========
        self.question_bank_path = question_bank_path

    # ============================================================================
    # 界面展示方法
    # ============================================================================

    def display_header(self, title):
        """
        显示带格式的标题头

        Args:
            title: 标题文本
        """
        print("\n" + "=" * 60)
        print(f" {title}")
        print("=" * 60)

    def display_menu(self, options, title="请选择"):
        """
        显示菜单并获取用户选择

        Args:
            options: 选项列表
            title: 提示标题

        Returns:
            tuple: (选择的编号, 选择的文本)
        """
        print(f"\n{title}:")
        for i, option in enumerate(options, 1):
            print(f"  {i}. {option}")

        while True:
            choice = input("\n请输入选项编号: ").strip()
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(options):
                    return idx + 1, options[idx]
                print("选项无效，请重新输入")
            except ValueError:
                print("请输入有效的数字")

    # ============================================================================
    # 成绩查询方法 - 核心修复：遍历所有课程成绩页
    # ============================================================================

    def load_completed_chapters(self):
        """
        从各课程成绩页面加载已完成的章节

        访问每个课程的 exam_list/course_id/{course_id}.html 页面，
        解析出该课程所有章节的完成状态和分数。

        成绩页面格式：
        - /index/exam/exam_list/course_id/{course_id}.html
        - 包含章节名称、成绩分数、完成时间等信息
        - 有成绩分数的章节 = 已完成

        Returns:
            dict: 已完成章节信息 {exam_id: {"name": 章节名, "score": 分数, "course_id": course_id}}
        """
        self.display_header("查询答题成绩")

        # 确保已登录
        if not self.check_login_status():
            print("[提示] 未登录，正在尝试自动登录...")
            if not self.analyze_network_request():
                print("[错误] 登录失败，无法获取成绩")
                return {}
            print("[成功] 登录成功")

        # 先获取所有课程
        print("\n[1/2] 获取课程列表...")
        courses = self.get_all_courses()
        if not courses:
            print("[错误] 无法获取课程列表")
            return {}

        print(f"[成功] 找到 {len(courses)} 个课程")

        # 存储已完成章节
        completed_chapters = {}

        # 遍历每个课程的成绩页面
        print(f"\n[2/2] 正在查询各课程成绩...")

        for course in courses:
            course_id = course["id"]
            course_name = course["name"]

            try:
                # 访问课程成绩页面
                result_url = (
                    f"{self.base_url}/index/exam/exam_list/course_id/{course_id}.html"
                )
                response = self.session.get(result_url, timeout=15)

                if response.status_code != 200:
                    print(f"  [{course_name}] 访问失败: HTTP {response.status_code}")
                    continue

                # 检查是否重定向到登录页
                if "login" in response.url.lower():
                    print(f"  [{course_name}] 需要登录，跳过")
                    continue

                # 解析HTML
                soup = BeautifulSoup(response.text, "html.parser")

                # 查找章节列表
                found_in_course = 0

                # 方法1: 查找包含成绩信息的链接
                for link in soup.find_all("a", href=True):
                    href = link.get("href", "")
                    text = link.get_text(strip=True)

                    # 匹配章节详情链接: /index/exam/show/id/{exam_id}.html
                    match = re.search(r"/index/exam/show/id/(\d+)", href)
                    if match:
                        exam_id = int(match.group(1))

                        # 检查这个链接附近是否有成绩/分数信息
                        parent = link.parent
                        parent_text = parent.get_text() if parent else ""

                        # 查找分数模式: 85分、90分、合格等
                        score_match = re.search(r"(\d+)\s*分", parent_text)
                        if score_match:
                            score = score_match.group(1)
                            if exam_id not in completed_chapters:
                                completed_chapters[exam_id] = {
                                    "name": text,
                                    "score": score,
                                    "course_id": course_id,
                                    "course_name": course_name,
                                }
                                found_in_course += 1

                # 方法2: 查找表格中的成绩数据（正确的表格解析）
                # 表格结构：课程 | 章节 | 完成时间 | 成绩 | 查看
                tables = soup.find_all("table")
                for table in tables:
                    rows = table.find_all("tr")
                    for row in rows:
                        cells = row.find_all(["td", "th"])
                        if len(cells) < 5:
                            continue

                        # 提取各列数据
                        course_name_col = cells[0].get_text(strip=True)  # 课程
                        chapter_name = cells[1].get_text(strip=True)  # 章节
                        complete_time = cells[2].get_text(strip=True)  # 完成时间
                        score = cells[3].get_text(strip=True)  # 成绩

                        # 跳过表头
                        if chapter_name == "章节" or chapter_name == "章节名称":
                            continue

                        # 检查是否有成绩（成绩列必须是数字）
                        if not re.match(r"\d+", score):
                            continue

                        # 从整行HTML中查找exam_id
                        row_html = str(row)
                        exam_match = re.search(r"/index/exam/show/id/(\d+)", row_html)
                        if exam_match:
                            exam_id = int(exam_match.group(1))
                            if exam_id not in completed_chapters:
                                completed_chapters[exam_id] = {
                                    "name": chapter_name,
                                    "score": score,
                                    "course_id": course_id,
                                    "course_name": course_name,
                                }
                                found_in_course += 1

                if found_in_course > 0:
                    print(f"  [{course_name}] 发现 {found_in_course} 个已完成的章节")

            except Exception as e:
                print(f"  [{course_name}] 查询失败: {e}")
                continue

        self.completed_chapters = completed_chapters
        self.completed_chapters_loaded = True

        # 输出汇总
        print(f"\n[结果] 共发现 {len(completed_chapters)} 个已完成章节")

        if completed_chapters:
            print("\n已完成章节列表:")
            print("-" * 60)
            for exam_id, info in sorted(completed_chapters.items()):
                print(
                    f"  ID:{exam_id:4d} | 分数:{info['score']:3s}分 | {info['name'][:30]}"
                )
            print("-" * 60)

        return completed_chapters

    def get_completion_status(self, exam_id, chapter_name):
        """
        获取章节完成状态

        根据exam_id判断章节是否已完成答题
        规则：exam_id在已完成列表中 = 已完成，显示分数

        Args:
            exam_id: 章节考试ID
            chapter_name: 章节名称

        Returns:
            str: 完成状态字符串，如 "[已完成 85分]" 或 "[未完成]"
        """
        if exam_id in self.completed_chapters:
            info = self.completed_chapters[exam_id]
            return f"[已完成 {info['score']}分]"
        else:
            return "[未完成]"

    def is_chapter_completed(self, exam_id):
        """
        判断章节是否已完成

        Args:
            exam_id: 章节考试ID

        Returns:
            bool: 是否已完成
        """
        return exam_id in self.completed_chapters

    # ============================================================================
    # 题库相关方法
    # ============================================================================

    def is_target_course(self, course_name):
        """
        判断课程是否有本地题库

        Args:
            course_name: 课程名称

        Returns:
            bool: 是否有本地题库
        """
        for target in self.target_courses:
            if target in course_name:
                return True
        return False

    def load_question_bank(self, path=None):
        """
        加载本地题库

        从question_bank.json文件加载已收集的题目和答案

        Returns:
            dict: 题库数据
        """
        try:
            base_dir = Path(__file__).resolve().parent
            target_path = path or self.question_bank_path or "question_bank.json"
            target_path = Path(target_path)
            if not target_path.is_absolute():
                target_path = (base_dir / target_path).resolve()
            with open(target_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            print("[警告] 题库文件不存在")
            return {}
        except json.JSONDecodeError:
            print("[警告] 题库文件格式错误")
            return {}
        except Exception as e:
            print(f"[警告] 加载题库失败: {e}")
            return {}

    # ============================================================================
    # API搜题方法
    # ============================================================================

    def api_search(self, question_text, options=None, course_name=None, chapter=None):
        """
        通过API搜索答案

        向外部搜题API发送请求获取答案

        Args:
            question_text: 题目文本
            options: 选项列表
            course_name: 课程名称
            chapter: 章节名称

        Returns:
            str: 答案选项（如"A"、"B"等），未找到返回None
        """
        if not self.api_available:
            return None

        if self.api_error_count >= 3:
            print("  [API] 错误次数过多，已禁用")
            return None

        try:
            import requests

            data = {
                "questionId": f"q_{int(time.time())}",
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

            response = requests.post(
                self.api_endpoint,
                json=data,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "x-api-key": self.api_key,
                },
                timeout=15,
            )

            if response.status_code == 200:
                result = response.json()

                if (
                    isinstance(result, dict)
                    and result.get("success")
                    and "data" in result
                ):
                    answer = result["data"].get("correctAnswer")
                    if answer:
                        print(f"  [API] 答案: {answer}")
                        self.api_error_count = 0
                        return answer

            elif response.status_code == 404:
                print("  [API] 404 - 服务暂时不可用")
                self.api_error_count += 1

            elif response.status_code == 401:
                print("  [API] 401 - API密钥无效")
                self.api_error_count += 1

            else:
                print(f"  [API] HTTP {response.status_code}")
                self.api_error_count += 1

        except requests.exceptions.Timeout:
            print("  [API] 请求超时")
            self.api_error_count += 1

        except requests.exceptions.ConnectionError:
            print("  [API] 连接失败")
            self.api_error_count += 1

        except Exception as e:
            print(f"  [API] 错误: {str(e)[:30]}")
            self.api_error_count += 1

        if self.api_error_count >= 3:
            print("  [API] 已自动禁用搜题功能")
            self.api_available = False

        return None

    # ============================================================================
    # 查找答案方法
    # ============================================================================

    def find_answer(
        self, question_text, options=None, course_name=None, chapter_name=None
    ):
        """
        查找题目答案

        答题策略：
        1. 先在本地题库中查找
        2. 本地没有则调用API搜题

        Args:
            question_text: 题目文本
            options: 选项列表
            course_name: 课程名称
            chapter_name: 章节名称

        Returns:
            str: 答案选项，未找到返回None
        """
        # 策略1：在本地题库中查找
        for chapter_key, chapter_data in self.question_bank.items():
            for q in chapter_data.get("questions", []):
                if q.get("question_text") == question_text:
                    answer = q.get("selected_answer")
                    if answer:
                        return answer

        # 策略2：API搜题
        return self.api_search(question_text, options, course_name, chapter_name)

    # ============================================================================
    # 课程和章节获取方法
    # ============================================================================

    def get_all_courses(self):
        """
        获取所有课程列表

        Returns:
            list: 课程列表，每个元素包含id、name、href
        """
        try:
            url = f"{self.base_url}/index/exam/index.html"
            response = self.session.get(url, timeout=10)

            if response.status_code != 200:
                return []

            soup = BeautifulSoup(response.text, "html.parser")
            courses = []

            for widget in soup.find_all("div", class_="widget"):
                for link in widget.find_all("a", href=True):
                    href = link.get("href", "")
                    match = re.search(r"/index/exam/lists/course_id/(\d+)", href)
                    if match:
                        course_id = int(match.group(1))
                        heading = widget.find("h2", class_="widget-heading")
                        if heading:
                            course_name = heading.get_text(strip=True)
                            courses.append(
                                {
                                    "id": course_id,
                                    "name": course_name,
                                    "href": urljoin(self.base_url, href),
                                }
                            )
                        break

            seen = set()
            return [c for c in courses if not (c["id"] in seen or seen.add(c["id"]))]
        except Exception as e:
            print(f"[错误] 获取课程列表失败: {e}")
            return []

    def get_chapters(self, course_id):
        """
        获取课程的章节列表

        Args:
            course_id: 课程ID

        Returns:
            list: 章节列表，每个元素包含exam_id、name、href
        """
        try:
            url = f"{self.base_url}/index/exam/lists/course_id/{course_id}.html"
            response = self.session.get(url, timeout=10)

            if response.status_code != 200:
                return []

            soup = BeautifulSoup(response.text, "html.parser")
            chapters = []

            for a in soup.find_all("a", href=True):
                href = a.get("href", "")
                text = a.get_text(strip=True)
                if "/index/exam/show/id/" in href:
                    match = re.search(r"/index/exam/show/id/(\d+)", href)
                    if match:
                        exam_id = int(match.group(1))
                        chapters.append(
                            {"exam_id": exam_id, "name": text, "href": href}
                        )

            seen = set()
            return [
                c
                for c in chapters
                if not (c["exam_id"] in seen or seen.add(c["exam_id"]))
            ]
        except Exception as e:
            print(f"[错误] 获取章节列表失败: {e}")
            return []

    # ============================================================================
    # 自动答题核心方法
    # ============================================================================

    def auto_answer(self, exam_id, course_name, chapter_name):
        """
        自动完成单个章节的答题

        Args:
            exam_id: 章节考试ID
            course_name: 课程名称
            chapter_name: 章节名称

        Returns:
            bool: 是否成功完成答题
        """
        self.display_header(f"答题: {chapter_name}")

        try:
            url = f"{self.base_url}/index/exam/show/id/{exam_id}.html"
            response = self.session.get(url, timeout=10)

            if response.status_code != 200:
                print(f"[错误] 访问失败: {response.status_code}")
                return False

            soup = BeautifulSoup(response.text, "html.parser")
            form = soup.find("form", id="post_form")

            if not form:
                print("[错误] 未找到表单")
                return False

            submit_url = urljoin(self.base_url, form.get("action", ""))
            question_uls = soup.find_all("ul", class_="list-unstyled question")

            if not question_uls:
                print("[错误] 未找到题目")
                return False

            answer_data = {}
            local_count = 0
            api_count = 0
            failed_count = 0

            for idx, q_ul in enumerate(question_uls):
                title_li = q_ul.find("li", class_="question_title")
                if not title_li:
                    continue

                question_text = title_li.get_text(strip=True)

                options = []
                for li in q_ul.find_all("li", class_="question_info"):
                    input_tag = li.find("input", type=["radio", "checkbox"])
                    if input_tag:
                        options.append(
                            {
                                "text": li.get_text(strip=True),
                                "value": input_tag.get("value", ""),
                            }
                        )

                print(f"第 {idx + 1} 题: {question_text[:40]}...", end="", flush=True)

                answer = self.find_answer(
                    question_text, options, course_name, chapter_name
                )

                if answer:
                    input_tag = q_ul.find("input", type=["radio", "checkbox"])
                    if input_tag:
                        input_name = input_tag.get("name", "")
                        if input_name:
                            answer_data[input_name] = answer
                            if self.is_answer_in_local_bank(question_text):
                                local_count += 1
                            else:
                                api_count += 1
                            print(f" -> {answer}")
                else:
                    print(" -> 未找到")
                    failed_count += 1

            print(
                f"\n[统计] 本地: {local_count}, API: {api_count}, 未找到: {failed_count}"
            )

            if not answer_data:
                print("[错误] 没有找到任何答案")
                return False

            print("\n" + "=" * 50)
            confirm = input("确认提交？(y/n): ")
            if confirm.lower() != "y":
                print("[取消] 已取消")
                return False

            print("[提交] 正在提交...")
            response = self.session.post(submit_url, data=answer_data, timeout=10)

            if response.status_code == 200:
                print("[成功] 提交成功!")
                self.completed_chapters_loaded = False
                return True
            else:
                print(f"[失败] HTTP {response.status_code}")
                return False

        except Exception as e:
            print(f"[错误] {e}")
            return False

    def is_answer_in_local_bank(self, question_text):
        """
        检查答案是否来自本地题库

        Args:
            question_text: 题目文本

        Returns:
            bool: 是否在本地题库中
        """
        for chapter_key, chapter_data in self.question_bank.items():
            for q in chapter_data.get("questions", []):
                if q.get("question_text") == question_text:
                    return True
        return False

    # ============================================================================
    # 可编程答题方法（用于插件/自动化）
    # ============================================================================

    def auto_answer_with_report(
        self,
        exam_id,
        course_name,
        chapter_name,
        *,
        submit=True,
        strict=True,
        min_answer_rate=1.0,
    ):
        """
        自动答题并返回详细报告（非交互）

        Args:
            exam_id: 章节考试ID
            course_name: 课程名称
            chapter_name: 章节名称
            submit: 是否提交答案
            strict: 是否严格模式（未找到答案则不提交）
            min_answer_rate: 最低答题覆盖率（0~1）

        Returns:
            dict: 报告信息
        """
        report = {
            "success": False,
            "status": "error",
            "message": "",
            "stats": {
                "total": 0,
                "answered": 0,
                "local": 0,
                "api": 0,
                "missing": 0,
            },
            "missing_samples": [],
        }

        try:
            url = f"{self.base_url}/index/exam/show/id/{exam_id}.html"
            response = self.session.get(url, timeout=10)

            if response.status_code != 200:
                report["message"] = f"访问失败: HTTP {response.status_code}"
                return report

            soup = BeautifulSoup(response.text, "html.parser")
            form = soup.find("form", id="post_form")

            if not form:
                report["message"] = "未找到答题表单"
                return report

            submit_url = urljoin(self.base_url, form.get("action", ""))
            question_uls = soup.find_all("ul", class_="list-unstyled question")

            if not question_uls:
                report["message"] = "未找到题目列表"
                return report

            answer_data = {}
            local_count = 0
            api_count = 0
            failed_count = 0

            for idx, q_ul in enumerate(question_uls):
                title_li = q_ul.find("li", class_="question_title")
                if not title_li:
                    continue

                question_text = title_li.get_text(strip=True)

                options = []
                for li in q_ul.find_all("li", class_="question_info"):
                    input_tag = li.find("input", type=["radio", "checkbox"])
                    if input_tag:
                        options.append(
                            {
                                "text": li.get_text(strip=True),
                                "value": input_tag.get("value", ""),
                            }
                        )

                report["stats"]["total"] += 1

                answer = self.find_answer(
                    question_text, options, course_name, chapter_name
                )

                if answer:
                    input_tag = q_ul.find("input", type=["radio", "checkbox"])
                    if input_tag:
                        input_name = input_tag.get("name", "")
                        if input_name:
                            answer_data[input_name] = answer
                            report["stats"]["answered"] += 1
                            if self.is_answer_in_local_bank(question_text):
                                local_count += 1
                            else:
                                api_count += 1
                else:
                    failed_count += 1
                    if len(report["missing_samples"]) < 5:
                        report["missing_samples"].append(question_text[:60])

            report["stats"]["local"] = local_count
            report["stats"]["api"] = api_count
            report["stats"]["missing"] = failed_count

            if report["stats"]["total"] == 0:
                report["message"] = "未解析到题目"
                return report

            answered_rate = report["stats"]["answered"] / report["stats"]["total"]
            if strict and (report["stats"]["missing"] > 0):
                report["status"] = "insufficient_answers"
                report["message"] = "存在未找到答案的题目，已停止提交"
                return report
            if answered_rate < min_answer_rate:
                report["status"] = "insufficient_answers"
                report["message"] = "答题覆盖率未达标，已停止提交"
                return report

            if not submit:
                report["status"] = "prepared"
                report["message"] = "已生成答案，未提交"
                report["success"] = True
                return report

            if not answer_data:
                report["message"] = "未生成任何答案"
                return report

            response = self.session.post(submit_url, data=answer_data, timeout=10)
            if response.status_code == 200:
                report["status"] = "submitted"
                report["message"] = "提交成功"
                report["success"] = True
                self.completed_chapters_loaded = False
                return report

            report["message"] = f"提交失败: HTTP {response.status_code}"
            return report

        except Exception as e:
            report["message"] = f"答题异常: {e}"
            return report

    # ============================================================================
    # 主运行方法
    # ============================================================================

    def run(self):
        """
        主程序入口

        执行流程：
        1. 自动登录
        2. 加载题库
        3. 获取课程列表
        4. 加载已完成答题记录（从各课程成绩页获取）
        5. 选择课程和章节
        6. 自动答题
        7. 循环继续
        """
        self.display_header("智能答题系统")
        print("本地题库 + API搜题")
        print("支持查询真实答题成绩")

        # 步骤1: 登录
        print("\n[1/6] 登录...")
        if not self.check_login_status():
            if not self.analyze_network_request():
                print("[失败] 登录失败")
                return
        print("[成功] 登录成功")

        # 步骤2: 加载题库
        print("\n[2/6] 加载题库...")
        self.question_bank = self.load_question_bank()
        total_q = sum(len(v.get("questions", [])) for v in self.question_bank.values())
        print(f"[成功] 已加载 {total_q} 题")

        # 步骤3: 获取课程
        print("\n[3/6] 获取课程...")
        self.all_courses = self.get_all_courses()
        if not self.all_courses:
            print("[失败] 未找到课程")
            return
        print(f"[成功] 找到 {len(self.all_courses)} 个课程")

        # 步骤4: 加载已完成答题记录（核心修复）
        print("\n[4/6] 加载答题成绩...")
        self.load_completed_chapters()

        # 步骤5: 选择课程和章节
        self.display_header("选择课程")
        for i, course in enumerate(self.all_courses, 1):
            status = "[有题库]" if self.is_target_course(course["name"]) else "[需搜题]"
            print(f"  {i}. {course['name']} {status}")

        choice, text = self.display_menu(
            [c["name"] for c in self.all_courses], "选择课程"
        )
        selected = self.all_courses[choice - 1]
        print(f"\n已选择: {selected['name']}")

        print("\n[5/6] 获取章节...")
        chapters = self.get_chapters(selected["id"])
        if not chapters:
            print("[失败] 没有章节")
            return
        print(f"[成功] 找到 {len(chapters)} 个章节")

        # 显示章节列表（包含真实完成状态）
        self.display_header(f"{selected['name']} - 选择章节")
        for i, ch in enumerate(chapters, 1):
            status = self.get_completion_status(ch["exam_id"], ch["name"])
            print(f"  {i}. {ch['name']} {status}")

        mode, mode_text = self.display_menu(
            ["全部章节", "指定章节(1,3,5)", "范围(1-5)", "仅未完成", "返回"], "答题模式"
        )

        if mode == 5:
            self.run()
            return

        selected_chapters = []

        if mode == 1:
            selected_chapters = chapters
        elif mode == 2:
            nums = input("输入编号(逗号): ").strip()
            try:
                for n in nums.split(","):
                    idx = int(n) - 1
                    if 0 <= idx < len(chapters):
                        selected_chapters.append(chapters[idx])
            except:
                print("[错误] 格式错误")
                return
        elif mode == 3:
            rng = input("输入范围(如1-5): ").strip()
            try:
                start, end = map(int, rng.split("-"))
                selected_chapters = chapters[start - 1 : end]
            except:
                print("[错误] 范围错误")
                return
        elif mode == 4:
            selected_chapters = [
                ch for ch in chapters if ch["exam_id"] not in self.completed_chapters
            ]
            if not selected_chapters:
                print("[提示] 所有章节已完成！")
                return

        if not selected_chapters:
            print("[取消] 未选择章节")
            return

        print(f"\n已选择 {len(selected_chapters)} 个章节")

        # 步骤6: 开始答题
        self.display_header("开始答题")
        success = fail = 0

        for i, ch in enumerate(selected_chapters, 1):
            print(f"\n[{i}/{len(selected_chapters)}] {ch['name']}")
            if self.auto_answer(ch["exam_id"], selected["name"], ch["name"]):
                success += 1
            else:
                fail += 1
            time.sleep(0.5)

        # 步骤7: 完成统计
        self.display_header("完成")
        print(f"成功: {success}")
        print(f"失败: {fail}")
        print(f"总计: {len(selected_chapters)}")

        print("\n正在刷新成绩...")
        self.load_completed_chapters()

        cont = input("\n继续？(y/n): ")
        if cont.lower() == "y":
            self.run()
        else:
            print("\n再见!")


if __name__ == "__main__":
    try:
        bot = QuizBot(api_key="bot666")
        bot.run()
    except KeyboardInterrupt:
        print("\n\n已退出")
    except Exception as e:
        print(f"\n错误: {e}")
        import traceback

        traceback.print_exc()
