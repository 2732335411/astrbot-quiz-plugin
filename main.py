#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AstrBot 插件：智能答题（QQ）

说明：
- 每个 QQ 用户仅允许绑定一个平台账号（不可解绑）
- 任务并发最多 3 个，超出排队
- 管理指令仅管理员私信可见
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid
import threading
import importlib.util
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from astrbot.api import logger
from astrbot.api.event import (
    AstrMessageEvent,
    MessageChain,
    filter,
)
from astrbot.api.star import Context, Star, register

from cryptography.fernet import Fernet, InvalidToken

PLUGIN_DIR = Path(__file__).resolve().parent


def _load_quiz_bot():
    smart_quiz_path = PLUGIN_DIR / "smart_quiz_api.py"
    if not smart_quiz_path.exists():
        raise ModuleNotFoundError("smart_quiz_api.py not found in plugin directory")

    if str(PLUGIN_DIR) not in sys.path:
        sys.path.insert(0, str(PLUGIN_DIR))

    spec = importlib.util.spec_from_file_location("smart_quiz_api", smart_quiz_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["smart_quiz_api"] = module
    spec.loader.exec_module(module)
    return module.QuizBot


BASE_DIR = Path(__file__).resolve().parent


def _resolve_data_dir(plugin_name: str) -> Path:
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_data_path

        data_dir = get_astrbot_data_path() / "plugin_data" / plugin_name
    except Exception:
        data_dir = BASE_DIR / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def _now_str() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def _mask_text(text: str, keep: int = 2) -> str:
    if not text:
        return ""
    if len(text) <= keep:
        return "*" * len(text)
    return text[:keep] + "*" * (len(text) - keep)


@dataclass
class QuizTask:
    task_id: str
    qq_id: str
    sender_name: str
    umo: str
    course_id: Optional[int]
    course_name: Optional[str]
    mode: str
    spec: str
    status: str = "queued"
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    summary: Dict = field(default_factory=dict)
    error: Optional[str] = None


class BindingStore:
    def __init__(self, path: Path, key_path: Path) -> None:
        self._path = path
        self._key_path = key_path
        self._lock = threading.Lock()
        self._fernet = self._load_or_create_key()
        self._data = self._load()

    def _load_or_create_key(self) -> Fernet:
        if self._key_path.exists():
            key = self._key_path.read_bytes()
            return Fernet(key)
        key = Fernet.generate_key()
        self._key_path.write_bytes(key)
        return Fernet(key)

    def _load(self) -> Dict:
        if not self._path.exists():
            return {"version": 1, "bindings": {}}
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return {"version": 1, "bindings": {}}

    def _save(self) -> None:
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._path)

    def bind(self, qq_id: str, username: str, password: str) -> Tuple[bool, str]:
        with self._lock:
            bindings = self._data.setdefault("bindings", {})
            if qq_id in bindings:
                existing = bindings[qq_id]
                if existing.get("username") != username:
                    return False, "你已绑定其他账号，按规则不可解绑或更换。"
                existing["password"] = self._encrypt(password)
                existing["updated_at"] = _now_str()
                self._save()
                return True, "已更新密码（账号未变更）。"

            bindings[qq_id] = {
                "username": username,
                "password": self._encrypt(password),
                "bound_at": _now_str(),
                "updated_at": _now_str(),
            }
            self._save()
            return True, "绑定成功。此账号将永久绑定，无法解绑或更换。"

    def get(self, qq_id: str) -> Optional[Dict]:
        with self._lock:
            data = self._data.get("bindings", {}).get(qq_id)
            if not data:
                return None
            try:
                return {
                    "username": data.get("username", ""),
                    "password": self._decrypt(data.get("password", "")),
                    "bound_at": data.get("bound_at", ""),
                    "updated_at": data.get("updated_at", ""),
                }
            except InvalidToken:
                return None

    def list_safe(self) -> List[Dict]:
        with self._lock:
            result = []
            for qq_id, info in self._data.get("bindings", {}).items():
                result.append(
                    {
                        "qq_id": qq_id,
                        "username": info.get("username", ""),
                        "bound_at": info.get("bound_at", ""),
                        "updated_at": info.get("updated_at", ""),
                    }
                )
            return result

    def _encrypt(self, text: str) -> str:
        token = self._fernet.encrypt(text.encode("utf-8"))
        return token.decode("utf-8")

    def _decrypt(self, token: str) -> str:
        return self._fernet.decrypt(token.encode("utf-8")).decode("utf-8")


@register(
    "smart_quiz_bot",
    "AI Assistant",
    "面向QQ用户的智能答题插件（AstrBot）",
    "1.0.1",
)
class SmartQuizPlugin(Star):
    def __init__(self, context: Context, config: Dict):
        super().__init__(context)
        self.config = config or {}
        plugin_name = getattr(self, "name", "smart_quiz_bot")
        data_dir = _resolve_data_dir(plugin_name)
        self.bindings = BindingStore(data_dir / "bindings.json", data_dir / "secret.key")
        self._quiz_bot_cls = None
        self._queue: asyncio.Queue[QuizTask] = asyncio.Queue()
        self._tasks: Dict[str, QuizTask] = {}
        self._workers: List[asyncio.Task] = []
        self._workers_started = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._task_lock = asyncio.Lock()
        self._course_cache: Dict[str, Dict] = {}
        self._chapter_cache: Dict[str, Dict] = {}

    # --------------------------
    # 生命周期
    # --------------------------
    @filter.on_astrbot_loaded()
    async def _on_loaded(self):
        self._loop = asyncio.get_running_loop()
        await self._ensure_workers()

    async def _ensure_workers(self):
        if self._workers_started:
            return
        self._workers_started = True
        for idx in range(self._max_concurrency()):
            self._workers.append(asyncio.create_task(self._worker_loop(idx)))
        logger.info(f"[答题插件] 已启动 {self._max_concurrency()} 个任务工作协程")

    def _max_concurrency(self) -> int:
        value = int(self.config.get("max_concurrency", 3))
        if value < 1:
            value = 1
        if value > 3:
            value = 3
        return value

    # --------------------------
    # 消息入口
    # --------------------------
    @filter.command("答题")
    async def on_quiz_command(self, event: AstrMessageEvent, args=None, kwargs=None):
        await self._ensure_workers()
        self._cleanup_tasks()

        args = self._normalize_args(event, args, "答题")
        if not args or args[0] in {"帮助", "help", "?", "菜单"}:
            yield event.plain_result(self._help_text(event))
            return

        cmd = args[0]
        if cmd == "绑定":
            yield event.plain_result(await self._handle_bind(event, args[1:]))
            return
        if cmd == "课程":
            yield event.plain_result(await self._handle_courses(event))
            return
        if cmd == "章节":
            yield event.plain_result(await self._handle_chapters(event, args[1:]))
            return
        if cmd == "开始":
            yield event.plain_result(await self._handle_start(event, args[1:]))
            return
        if cmd == "状态":
            yield event.plain_result(self._handle_status(event))
            return
        if cmd == "取消":
            yield event.plain_result(self._handle_cancel(event, args[1:]))
            return

        yield event.plain_result(self._help_text(event))

    @filter.command("绑定")
    async def on_bind_command(self, event: AstrMessageEvent, args=None, kwargs=None):
        await self._ensure_workers()
        args = self._normalize_args(event, args, "绑定")
        yield event.plain_result(await self._handle_bind(event, args))

    @filter.command("课程")
    async def on_courses_command(self, event: AstrMessageEvent, args=None, kwargs=None):
        await self._ensure_workers()
        yield event.plain_result(await self._handle_courses(event))

    @filter.command("章节")
    async def on_chapters_command(self, event: AstrMessageEvent, args=None, kwargs=None):
        await self._ensure_workers()
        args = self._normalize_args(event, args, "章节")
        yield event.plain_result(await self._handle_chapters(event, args))

    @filter.command("开始")
    async def on_start_command(self, event: AstrMessageEvent, args=None, kwargs=None):
        await self._ensure_workers()
        args = self._normalize_args(event, args, "开始")
        yield event.plain_result(await self._handle_start(event, args))

    @filter.command("状态")
    async def on_status_command(self, event: AstrMessageEvent, args=None, kwargs=None):
        await self._ensure_workers()
        yield event.plain_result(self._handle_status(event))

    @filter.command("取消")
    async def on_cancel_command(self, event: AstrMessageEvent, args=None, kwargs=None):
        await self._ensure_workers()
        args = self._normalize_args(event, args, "取消")
        yield event.plain_result(self._handle_cancel(event, args))

    @filter.command("答题管理")
    async def on_admin_command(self, event: AstrMessageEvent, args=None, kwargs=None):
        await self._ensure_workers()
        self._cleanup_tasks()

        if not self._is_private(event):
            yield event.plain_result("管理命令仅支持私信使用。")
            return
        if not self._is_admin(event):
            yield event.plain_result("仅管理员可用该命令。")
            return

        args = self._normalize_args(event, args, "答题管理")
        if not args or args[0] in {"帮助", "help", "?"}:
            yield event.plain_result(self._admin_help_text())
            return

        cmd = args[0]
        if cmd in {"状态", "列表"}:
            yield event.plain_result(self._admin_task_list())
            return
        if cmd == "详情":
            yield event.plain_result(self._admin_task_detail(args[1:]))
            return
        if cmd == "取消":
            yield event.plain_result(self._admin_cancel(args[1:]))
            return
        if cmd == "绑定":
            yield event.plain_result(self._admin_list_bindings())
            return

        yield event.plain_result(self._admin_help_text())

    # --------------------------
    # 命令处理
    # --------------------------
    async def _handle_bind(self, event: AstrMessageEvent, args: List[str]) -> str:
        if not self._is_private(event):
            return "绑定含敏感信息，请私信我进行绑定。"
        if len(args) < 2:
            return "用法：/答题 绑定 <用户名> <密码>"
        username, password = args[0], args[1]
        ok, msg = self.bindings.bind(event.get_sender_id(), username, password)
        return msg

    async def _handle_courses(self, event: AstrMessageEvent) -> str:
        binding = self.bindings.get(event.get_sender_id())
        if not binding:
            return "你还没有绑定账号，请先私信 /答题 绑定 <用户名> <密码>"
        if not self._allow_group_commands(event):
            return "请私信使用此命令，或由管理员在插件配置 allow_group_ids 中添加当前群号。"

        try:
            courses, target_courses = await asyncio.to_thread(
                self._fetch_courses_sync, binding["username"], binding["password"]
            )
        except Exception as e:
            return f"获取课程失败：{e}"

        if not courses:
            return "未获取到课程，请稍后重试。"

        self._course_cache[event.get_sender_id()] = {
            "courses": courses,
            "timestamp": time.time(),
        }

        lines = ["课程列表："]
        for idx, course in enumerate(courses, 1):
            tag = "[有题库]" if self._is_target_course(course["name"], target_courses) else "[需搜题]"
            lines.append(f"{idx}. {course['name']} | ID:{course['id']} {tag}")
        lines.append("提示：使用 /答题 章节 <课程序号或课程ID> 查看章节。")
        return "\n".join(lines)

    async def _handle_chapters(self, event: AstrMessageEvent, args: List[str]) -> str:
        binding = self.bindings.get(event.get_sender_id())
        if not binding:
            return "你还没有绑定账号，请先私信 /答题 绑定 <用户名> <密码>"
        if not self._allow_group_commands(event):
            return "请私信使用此命令，或由管理员在插件配置 allow_group_ids 中添加当前群号。"
        if not args:
            return "用法：/答题 章节 <课程序号或课程ID>"

        try:
            course_id, course_name = await self._resolve_course(
                event.get_sender_id(), args[0], binding
            )
        except Exception as e:
            return f"课程解析失败：{e}"

        try:
            result = await asyncio.to_thread(
                self._fetch_chapters_sync,
                binding["username"],
                binding["password"],
                course_id,
            )
        except Exception as e:
            return f"获取章节失败：{e}"

        chapters = result["chapters"]
        completed = result["completed"]
        course_name = result.get("course_name") or course_name

        if not chapters:
            return "未获取到章节，请稍后重试。"

        self._chapter_cache[event.get_sender_id()] = {
            "course_id": course_id,
            "course_name": course_name,
            "chapters": chapters,
            "timestamp": time.time(),
        }

        lines = [f"章节列表：{course_name}"]
        for idx, ch in enumerate(chapters, 1):
            status = "[已完成]" if ch["exam_id"] in completed else "[未完成]"
            lines.append(f"{idx}. {ch['name']} | ID:{ch['exam_id']} {status}")
        lines.append("提示：使用 /答题 开始 <课程序号或课程ID> <模式> [参数] 开始答题。")
        lines.append("模式：全部 / 未完成 / 指定 1,3,5 / 范围 1-5")
        return "\n".join(lines)

    async def _handle_start(self, event: AstrMessageEvent, args: List[str]) -> str:
        binding = self.bindings.get(event.get_sender_id())
        if not binding:
            return "你还没有绑定账号，请先私信 /答题 绑定 <用户名> <密码>"
        if not self._allow_group_commands(event):
            return "请私信使用此命令，或由管理员在插件配置 allow_group_ids 中添加当前群号。"
        if not args:
            return "用法：/答题 开始 <课程序号或课程ID> <模式> [参数]，或 /答题 开始 <模式> [参数]"

        course_token = args[0]
        if not course_token.isdigit() and self._is_mode_token(course_token):
            cached = self._chapter_cache.get(event.get_sender_id())
            if not cached:
                return "请先使用 /答题 章节 <课程序号或课程ID> 获取章节列表"
            course_id = cached.get("course_id")
            course_name = cached.get("course_name") or "已选课程"
            mode, spec = self._parse_mode(args)
        else:
            try:
                course_id, course_name = await self._resolve_course(
                    event.get_sender_id(), course_token, binding
                )
            except Exception as e:
                return f"课程解析失败：{e}"

            mode, spec = self._parse_mode(args[1:])

        if mode in {"指定", "范围"} and not spec:
            return "请提供章节参数，例如：/答题 开始 1 指定 1,3,5 或 /答题 开始 1 范围 1-5"

        if self._has_active_task(event.get_sender_id()):
            return "你已有进行中的任务，请等待完成或使用 /答题 状态 查看。"

        task_id = uuid.uuid4().hex[:8]
        task = QuizTask(
            task_id=task_id,
            qq_id=event.get_sender_id(),
            sender_name=event.get_sender_name(),
            umo=event.unified_msg_origin,
            course_id=course_id,
            course_name=course_name,
            mode=mode,
            spec=spec,
        )

        async with self._task_lock:
            self._tasks[task_id] = task
            await self._queue.put(task)
            position = self._queue.qsize()

        safety_tip = "注意：默认跳过未识别题目并继续答题。"
        return (
            f"任务已加入队列，ID: {task_id}，当前排队位置: {position}\n"
            f"课程：{course_name} | 模式：{mode} {spec}\n"
            f"{safety_tip}"
        )

    def _handle_status(self, event: AstrMessageEvent) -> str:
        qq_id = event.get_sender_id()
        tasks = [t for t in self._tasks.values() if t.qq_id == qq_id]
        if not tasks:
            return "你当前没有任务。"
        lines = ["你的任务列表："]
        for t in sorted(tasks, key=lambda x: x.created_at, reverse=True):
            status = t.status
            lines.append(f"{t.task_id} | {status} | {t.course_name or t.course_id}")
        return "\n".join(lines)

    def _handle_cancel(self, event: AstrMessageEvent, args: List[str]) -> str:
        if not args:
            return "用法：/答题 取消 <任务ID>"
        task_id = args[0]
        task = self._tasks.get(task_id)
        if not task or task.qq_id != event.get_sender_id():
            return "未找到你的任务。"
        if task.status in {"completed", "failed", "canceled"}:
            return "该任务已结束，无法取消。"
        task.cancel_event.set()
        if task.status == "queued":
            task.status = "canceled"
            task.finished_at = time.time()
            return "已取消排队中的任务。"
        task.status = "canceling"
        return "已提交取消请求，任务将尽快停止。"

    # --------------------------
    # 管理命令
    # --------------------------
    def _admin_help_text(self) -> str:
        return (
            "管理命令（仅管理员私信可用）：\n"
            "/答题管理 状态|列表  查看任务队列\n"
            "/答题管理 详情 <任务ID>\n"
            "/答题管理 取消 <任务ID>\n"
            "/答题管理 绑定  查看绑定列表（脱敏）"
        )

    def _admin_task_list(self) -> str:
        if not self._tasks:
            return "当前没有任务。"
        lines = ["任务列表："]
        for t in sorted(self._tasks.values(), key=lambda x: x.created_at, reverse=True):
            lines.append(
                f"{t.task_id} | {t.status} | {t.sender_name} | {t.course_name or t.course_id}"
            )
        lines.append(f"排队中: {self._queue.qsize()} | 并发上限: {self._max_concurrency()}")
        return "\n".join(lines)

    def _admin_task_detail(self, args: List[str]) -> str:
        if not args:
            return "用法：/答题管理 详情 <任务ID>"
        task = self._tasks.get(args[0])
        if not task:
            return "未找到任务。"
        lines = [
            f"任务ID: {task.task_id}",
            f"状态: {task.status}",
            f"用户: {task.sender_name} ({task.qq_id})",
            f"课程: {task.course_name or task.course_id}",
            f"模式: {task.mode} {task.spec}",
            f"创建: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(task.created_at))}",
        ]
        if task.summary:
            lines.append(f"结果: {task.summary}")
        if task.error:
            lines.append(f"错误: {task.error}")
        return "\n".join(lines)

    def _admin_cancel(self, args: List[str]) -> str:
        if not args:
            return "用法：/答题管理 取消 <任务ID>"
        task = self._tasks.get(args[0])
        if not task:
            return "未找到任务。"
        if task.status in {"completed", "failed", "canceled"}:
            return "该任务已结束。"
        task.cancel_event.set()
        if task.status == "queued":
            task.status = "canceled"
            task.finished_at = time.time()
            return "已取消排队中的任务。"
        task.status = "canceling"
        return "已提交取消请求，任务将尽快停止。"

    def _admin_list_bindings(self) -> str:
        bindings = self.bindings.list_safe()
        if not bindings:
            return "暂无绑定记录。"
        lines = ["绑定列表（脱敏）："]
        for b in bindings:
            masked = _mask_text(b["username"], keep=2)
            lines.append(f"{b['qq_id']} | {masked} | {b['bound_at']}")
        return "\n".join(lines)

    # --------------------------
    # 任务执行
    # --------------------------
    async def _worker_loop(self, idx: int):
        while True:
            task = await self._queue.get()
            try:
                if task.cancel_event.is_set():
                    task.status = "canceled"
                    task.finished_at = time.time()
                    continue
                await self._run_task(task)
            finally:
                self._queue.task_done()

    async def _run_task(self, task: QuizTask):
        task.status = "running"
        task.started_at = time.time()
        await self._send_text(task.umo, f"任务开始：{task.course_name or task.course_id}（ID: {task.task_id}）")

        try:
            result = await asyncio.to_thread(self._execute_task_sync, task)
            task.summary = result.get("summary", {})
            task.error = result.get("error")
            if result.get("canceled"):
                task.status = "canceled"
            else:
                task.status = "completed" if result.get("success") else "failed"
        except Exception as e:
            task.status = "failed"
            task.error = str(e)
        finally:
            task.finished_at = time.time()

        await self._send_text(task.umo, self._format_task_result(task))

    def _execute_task_sync(self, task: QuizTask) -> Dict:
        binding = self.bindings.get(task.qq_id)
        if not binding:
            return {"success": False, "error": "未找到绑定账号"}

        bot = self._create_bot(binding["username"], binding["password"])

        if not bot.check_login_status():
            if not bot.analyze_network_request():
                return {"success": False, "error": "登录失败"}

        bot.question_bank = bot.load_question_bank()

        courses = bot.get_all_courses()
        course = next((c for c in courses if c["id"] == task.course_id), None)
        if not course:
            return {"success": False, "error": "课程不存在或已变更"}
        task.course_name = course["name"]

        chapters = bot.get_chapters(task.course_id)
        if not chapters:
            return {"success": False, "error": "未获取到章节"}

        if task.mode == "未完成":
            bot.load_completed_chapters()
            chapters = [ch for ch in chapters if ch["exam_id"] not in bot.completed_chapters]
        elif task.mode == "指定":
            indices = self._parse_index_list(task.spec)
            chapters = [chapters[i - 1] for i in indices if 0 < i <= len(chapters)]
        elif task.mode == "范围":
            start, end = self._parse_range(task.spec)
            chapters = chapters[start - 1 : end]
        elif task.mode == "全部":
            pass
        else:
            return {"success": False, "error": "未知模式"}

        if not chapters:
            return {"success": False, "error": "未选中章节"}

        strict_mode = bool(self.config.get("strict_mode", False))
        auto_submit = bool(self.config.get("auto_submit", True))
        min_rate = float(self.config.get("min_answer_rate", 0.0))

        success_count = 0
        fail_count = 0
        skipped_count = 0
        submit_failed = 0
        invalid_count = 0
        failure_examples = []
        stopped_reason = None
        canceled = False

        for ch in chapters:
            if task.cancel_event.is_set():
                stopped_reason = "任务已取消"
                canceled = True
                break

            report = bot.auto_answer_with_report(
                ch["exam_id"],
                task.course_name,
                ch["name"],
                submit=auto_submit,
                strict=strict_mode,
                min_answer_rate=min_rate,
            )

            stats = report.get("stats", {}) or {}
            skipped_count += int(stats.get("missing", 0))
            invalid_count += int(stats.get("invalid", 0))

            if report["success"]:
                success_count += 1
            else:
                fail_count += 1
                if report.get("message") and len(failure_examples) < 3:
                    failure_examples.append(f"{ch['name']}: {report.get('message')}")
                if report.get("http_status") and report.get("http_status") != 200:
                    submit_failed += 1
                if report.get("status") == "insufficient_answers":
                    stopped_reason = report.get("message")
                    if strict_mode:
                        break

        if not stopped_reason and fail_count > 0:
            if failure_examples:
                stopped_reason = "章节失败：" + "；".join(failure_examples)
            else:
                stopped_reason = f"有 {fail_count} 个章节失败"

        summary = {
            "success": success_count,
            "failed": fail_count,
            "total": success_count + fail_count,
            "skipped": skipped_count,
            "invalid": invalid_count,
            "submit_failed": submit_failed,
            "stopped": stopped_reason,
        }

        return {
            "success": fail_count == 0 and not stopped_reason,
            "summary": summary,
            "error": stopped_reason,
            "canceled": canceled,
        }

    # --------------------------
    # 工具方法
    # --------------------------
    def _help_text(self, event: AstrMessageEvent) -> str:
        lines = [
            "智能答题指令：",
            "/绑定 <用户名> <密码>  （仅私信）",
            "/课程",
            "/章节 <课程序号或课程ID>",
            "/开始 <课程序号或课程ID> <模式> [参数]",
            "/开始 <模式> [参数]  （需先执行 /章节）",
            "/状态",
            "/取消 <任务ID>",
            "/答题 绑定 <用户名> <密码>  （仅私信）",
            "/答题 课程",
            "/答题 章节 <课程序号或课程ID>",
            "/答题 开始 <课程序号或课程ID> <模式> [参数]",
            "/答题 开始 <模式> [参数]  （需先执行 /答题 章节）",
            "/答题 状态",
            "/答题 取消 <任务ID>",
            "模式：全部 / 未完成 / 指定 1,3,5 / 范围 1-5",
            "群聊需管理员在配置 allow_group_ids 添加群号后使用。",
        ]
        if self._is_private(event) and self._is_admin(event):
            lines.append("管理员命令：/答题管理")
        return "\n".join(lines)

    def _format_task_result(self, task: QuizTask) -> str:
        if task.status == "canceled":
            return f"任务已取消：{task.task_id}"
        if task.status == "failed":
            return f"任务失败：{task.task_id}\n原因：{task.error or '未知错误'}"
        summary = task.summary or {}
        skipped = summary.get("skipped", 0)
        invalid = summary.get("invalid", 0)
        submit_failed = summary.get("submit_failed", 0)
        extra_parts = []
        if skipped:
            extra_parts.append(f"跳过: {skipped}")
        if invalid:
            extra_parts.append(f"异常选项: {invalid}")
        if submit_failed:
            extra_parts.append(f"提交失败: {submit_failed}")
        extra_text = f" {' | '.join(extra_parts)}" if extra_parts else ""
        return (
            f"任务完成：{task.task_id}\n"
            f"成功: {summary.get('success', 0)} 失败: {summary.get('failed', 0)} 总计: {summary.get('total', 0)}{extra_text}\n"
            f"停止原因: {summary.get('stopped') or '无'}"
        )

    async def _send_text(self, umo: str, text: str) -> None:
        try:
            await self.context.send_message(umo, MessageChain().message(text))
        except Exception:
            await self.context.send_message(umo, text)

    def _parse_args(self, text: str, command_name: str = "答题") -> List[str]:
        content = (text or "").strip()
        if not content:
            return []
        parts = content.split()
        if parts:
            first = parts[0].lstrip("/")
            if first == command_name:
                parts = parts[1:]
        return parts

    def _normalize_args(self, event: AstrMessageEvent, raw_args, command_name: str) -> List[str]:
        if raw_args is not None:
            if isinstance(raw_args, (list, tuple)):
                if len(raw_args) == 1 and isinstance(raw_args[0], (list, tuple)):
                    return [str(x) for x in raw_args[0]]
                return [str(x) for x in raw_args if x is not None]
            if isinstance(raw_args, str):
                return raw_args.split()
        return self._parse_args(event.message_str, command_name)

    def _is_private(self, event: AstrMessageEvent) -> bool:
        try:
            return event.message_obj.group_id in {None, "", "0"}
        except Exception:
            return False

    def _allow_group_commands(self, event: AstrMessageEvent) -> bool:
        if self._is_private(event):
            return True
        group_id = None
        try:
            group_id = str(event.message_obj.group_id)
        except Exception:
            group_id = None
        allow_ids = self.config.get("allow_group_ids", []) or []
        allow_ids = {str(item) for item in allow_ids}
        if group_id and group_id in allow_ids:
            return True
        return bool(self.config.get("allow_group_commands", False))

    def _is_admin(self, event: AstrMessageEvent) -> bool:
        sender_id = None
        try:
            sender_id = event.get_sender_id()
        except Exception:
            sender_id = None
        if sender_id:
            admin_ids = self.config.get("admin_ids", []) or []
            if str(sender_id) in {str(x) for x in admin_ids}:
                return True
        try:
            admins = getattr(self.context, "admins", None)
            if admins and sender_id in admins:
                return True
        except Exception:
            pass
        try:
            role = getattr(event.message_obj, "sender_role", None)
            if role in {"admin", "owner"}:
                return True
        except Exception:
            pass
        return False

    def _cleanup_tasks(self) -> None:
        expire_seconds = 24 * 3600
        now = time.time()
        stale_ids = [
            tid
            for tid, task in self._tasks.items()
            if task.finished_at and (now - task.finished_at) > expire_seconds
        ]
        for tid in stale_ids:
            self._tasks.pop(tid, None)

    def _has_active_task(self, qq_id: str) -> bool:
        for task in self._tasks.values():
            if task.qq_id == qq_id and task.status in {"queued", "running"}:
                return True
        return False

    def _parse_mode(self, args: List[str]) -> Tuple[str, str]:
        if not args:
            return "未完成", ""
        mode = args[0]
        spec = " ".join(args[1:]).strip()
        if mode in {"全部", "所有", "全"}:
            return "全部", spec
        if mode in {"未完成", "未答", "未做"}:
            return "未完成", spec
        if mode in {"指定", "选择", "选"}:
            return "指定", spec
        if mode in {"范围", "区间"}:
            return "范围", spec
        if "," in mode:
            return "指定", mode
        if "-" in mode:
            return "范围", mode
        return "未完成", " ".join(args).strip()

    def _is_mode_token(self, token: str) -> bool:
        if token in {"全部", "所有", "全", "未完成", "未答", "未做", "指定", "选择", "选", "范围", "区间"}:
            return True
        if "," in token or "-" in token:
            return True
        return False

    def _parse_index_list(self, text: str) -> List[int]:
        result = []
        for item in text.split(","):
            item = item.strip()
            if not item:
                continue
            if item.isdigit():
                result.append(int(item))
        return sorted(set(result))

    def _parse_range(self, text: str) -> Tuple[int, int]:
        parts = text.split("-")
        if len(parts) != 2:
            raise ValueError("范围格式错误，应为 1-5")
        start = int(parts[0])
        end = int(parts[1])
        if start <= 0 or end <= 0 or start > end:
            raise ValueError("范围参数错误")
        return start, end

    async def _resolve_course(
        self, qq_id: str, token: str, binding: Dict
    ) -> Tuple[int, str]:
        cached = self._course_cache.get(qq_id)
        if token.isdigit() and cached:
            courses = cached.get("courses", [])
            course_id = int(token)
            by_id = next((c for c in courses if c["id"] == course_id), None)
            if by_id:
                return by_id["id"], by_id["name"]
            if 1 <= course_id <= len(courses):
                course = courses[course_id - 1]
                return course["id"], course["name"]

        if token.isdigit():
            course_id = int(token)
            if cached:
                course = next((c for c in cached.get("courses", []) if c["id"] == course_id), None)
                if course:
                    return course_id, course["name"]
            courses, _ = await asyncio.to_thread(
                self._fetch_courses_sync, binding["username"], binding["password"]
            )
            course = next((c for c in courses if c["id"] == course_id), None)
            if course:
                return course_id, course["name"]
            raise ValueError("课程ID不存在")

        if cached:
            course = next((c for c in cached.get("courses", []) if token in c["name"]), None)
            if course:
                return course["id"], course["name"]

        courses, _ = await asyncio.to_thread(
            self._fetch_courses_sync, binding["username"], binding["password"]
        )
        course = next((c for c in courses if token in c["name"]), None)
        if course:
            return course["id"], course["name"]
        raise ValueError("未找到匹配课程")

    def _is_target_course(self, course_name: str, target_courses: List[str]) -> bool:
        for target in target_courses:
            if target in course_name:
                return True
        return False

    def _get_quiz_bot_cls(self):
        if self._quiz_bot_cls is None:
            self._quiz_bot_cls = _load_quiz_bot()
        return self._quiz_bot_cls

    def _create_bot(self, username: str, password: str):
        question_bank_path = self.config.get("question_bank_path", "question_bank.json")
        api_key = self.config.get("api_key", "bot666")
        api_endpoint = self.config.get("api_endpoint", "http://8.155.30.94:5000/api/get_answer")
        quiz_cls = self._get_quiz_bot_cls()
        return quiz_cls(
            api_key=api_key,
            username=username,
            password=password,
            api_endpoint=api_endpoint,
            question_bank_path=question_bank_path,
        )

    def _fetch_courses_sync(
        self,
        username: str,
        password: str,
    ) -> Tuple[List[Dict], List[str]]:
        if username is None or password is None:
            raise ValueError("未提供登录信息")
        bot = self._create_bot(username, password)
        if not bot.check_login_status():
            if not bot.analyze_network_request():
                raise RuntimeError("登录失败")
        courses = bot.get_all_courses()
        return courses, bot.target_courses

    def _fetch_chapters_sync(self, username: str, password: str, course_id: int) -> Dict:
        bot = self._create_bot(username, password)
        if not bot.check_login_status():
            if not bot.analyze_network_request():
                raise RuntimeError("登录失败")
        courses = bot.get_all_courses()
        course_name = next((c["name"] for c in courses if c["id"] == course_id), "")
        bot.load_completed_chapters()
        chapters = bot.get_chapters(course_id)
        return {"chapters": chapters, "completed": bot.completed_chapters, "course_name": course_name}
