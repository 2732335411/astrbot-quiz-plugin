#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
三三制微教学平台自动登录模块

功能：
- 自动登录三三制平台（https://33.bxwxm.com.cn）
- 支持多种密码加密格式尝试
- 模拟浏览器行为避免被识别

核心类：
- SanSanZhiAutoLogin: 登录机器人

作者：AI Assistant
日期：2026-01-30
"""

import httpx
import time
import re
from urllib.parse import urljoin
import json
import hashlib
import random
import string


class SanSanZhiAutoLogin:
    """
    三三制平台自动登录器

    用于自动完成平台登录，维护登录会话状态
    """

    def __init__(self, username=None, password=None):
        """
        初始化登录器

        Args:
            username: 用户名，默认从配置文件读取
            password: 密码，默认从配置文件读取
        """
        # ========== 平台配置 ==========
        self.base_url = "https://33.bxwxm.com.cn"

        # ========== 账号配置 ==========
        # TODO: 实际使用时修改为真实账号
        self.username = username or ""
        self.password = password or ""

        # ========== 会话管理 ==========
        self.session = httpx.Client(follow_redirects=True, timeout=10.0)
        self.login_success_url = None  # 登录成功后的跳转URL

        # ========== 浏览器模拟配置 ==========
        # 设置请求头模拟真实浏览器访问
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Cache-Control": "max-age=0",
            }
        )

    # ============================================================================
    # Token和密钥获取方法
    # ============================================================================

    def get_csrf_token_and_key(self):
        """
        获取登录所需的CSRF token和key

        尝试从登录页面提取或生成登录所需的密钥

        Returns:
            str: 密钥字符串
        """
        try:
            # 访问登录页面获取cookies和可能的token
            login_page_url = urljoin(self.base_url, "/index/login/index.html")
            response = self.session.get(login_page_url)

            if response.status_code != 200:
                print(f"获取登录页面失败: {response.status_code}")
                return None

            html_content = response.text

            # 尝试从页面HTML中提取key
            # 平台可能将key放在input、var或JS变量中
            patterns = [
                r'name=["\']key["\'] value=["\']([^"\']+)["\']',
                r'var\s+key\s*=\s*["\']([^"\']+)["\']',
                r'["\']key["\']:\s*["\']([^"\']+)["\']',
                r'token["\']?\s*[:=]\s*["\']([^"\']+)["\']',
            ]

            for pattern in patterns:
                match = re.search(pattern, html_content, re.IGNORECASE)
                if match:
                    print(f"找到可能的key: {match.group(1)}")
                    return match.group(1)

            # 如果没有找到，生成一个key（部分平台可能动态生成）
            timestamp = str(int(time.time()))
            random_str = "".join(
                random.choices(string.ascii_letters + string.digits, k=8)
            )
            generated_key = f"{timestamp}_{random_str}"

            print(f"生成key: {generated_key}")
            return generated_key

        except Exception as e:
            print(f"获取token时出错: {e}")
            return None

    # ============================================================================
    # 密码加密方法
    # ============================================================================

    def generate_password_hash(self, password, key=None):
        """
        生成密码哈希

        尝试多种加密格式，因为不同平台使用不同密码处理方式

        Args:
            password: 原始密码
            key: 登录key，可选

        Returns:
            list: 多种加密格式的密码列表
        """
        try:
            candidates = [
                password,  # 原始密码
                hashlib.md5(password.encode()).hexdigest(),  # MD5
                hashlib.sha1(password.encode()).hexdigest(),  # SHA1
                hashlib.sha256(password.encode()).hexdigest(),  # SHA256
            ]

            # 如果有key，尝试组合加密
            if key:
                candidates.extend(
                    [
                        hashlib.md5((password + key).encode()).hexdigest(),
                        hashlib.md5((key + password).encode()).hexdigest(),
                        hashlib.sha1((password + key).encode()).hexdigest(),
                        hashlib.sha256((password + key).encode()).hexdigest(),
                    ]
                )

            return candidates
        except Exception as e:
            print(f"生成密码哈希时出错: {e}")
            return [password]

    # ============================================================================
    # 登录测试方法
    # ============================================================================

    def human_like_delay(self, min_seconds=1, max_seconds=3):
        """
        模拟人类操作延迟

        Args:
            min_seconds: 最小延迟秒数
            max_seconds: 最大延迟秒数
        """
        delay = random.uniform(min_seconds, max_seconds)
        print(f"等待 {delay:.2f} 秒...")
        time.sleep(delay)

    def test_login_with_password_format(self, password_format, key):
        """
        测试特定密码格式的登录

        Args:
            password_format: 加密后的密码
            key: 登录key

        Returns:
            bool: 登录是否成功
        """
        login_url = urljoin(self.base_url, "/index/login/index.html")

        # 构建登录数据
        data = {
            "username": self.username,
            "password": password_format,
            "type": "password",
        }

        if key:
            data["key"] = key

        try:
            # 设置POST请求头
            headers = {
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": self.base_url,
                "Referer": urljoin(self.base_url, "/index/login/index.html"),
                "X-Requested-With": "XMLHttpRequest",
            }

            print(f"尝试登录 - 密码格式: {password_format[:20]}...")
            response = self.session.post(
                login_url, data=data, headers=headers, follow_redirects=False
            )

            print(f"响应状态码: {response.status_code}")

            # ========== 响应解析 ==========
            response_text = response.text

            # 尝试解析JSON响应
            try:
                json_response = json.loads(response_text)
                print(f"JSON响应: {json_response}")

                # 成功响应：code=1且msg包含"成功"
                if json_response.get("code") == 1 and "成功" in json_response.get(
                    "msg", ""
                ):
                    print("登录成功！")
                    self.login_success_url = json_response.get(
                        "url", "/index/index/index.html"
                    )
                    return True
                elif json_response.get("code") == 0:
                    print(f"登录失败: {json_response.get('msg', '未知错误')}")
                    return False

            except json.JSONDecodeError:
                pass

            # 检查重定向（302/303）
            if response.status_code in [302, 303]:
                location = response.headers.get("Location", "")
                print(f"重定向到: {location}")
                if "login" not in location.lower():
                    print("登录成功！")
                    self.login_success_url = location
                    return True

            # 检查响应内容中的成功标识
            success_indicators = ["成功", "success", "欢迎", "dashboard", "index"]
            for indicator in success_indicators:
                if indicator in response_text.lower():
                    print(f"发现成功标识: {indicator}")
                    return True

            # 检查失败标识
            failure_indicators = ["错误", "error", "失败", "用户名", "密码", "验证码"]
            for indicator in failure_indicators:
                if indicator in response_text:
                    print(f"发现失败标识: {indicator}")
                    return False

            return False

        except Exception as e:
            print(f"登录请求出错: {e}")
            return False

    # ============================================================================
    # 核心登录方法
    # ============================================================================

    def analyze_network_request(self):
        """
        自动分析并尝试登录

        流程：
        1. 获取登录页面和key
        2. 生成多种密码格式
        3. 逐一尝试登录

        Returns:
            bool: 登录是否成功
        """
        print("分析登录请求模式...")

        # 1. 获取key
        key = self.get_csrf_token_and_key()

        # 2. 生成密码格式列表
        password_candidates = self.generate_password_hash(self.password, key)

        print(f"将尝试 {len(password_candidates)} 种密码格式")

        # 3. 逐一测试
        for i, password_format in enumerate(password_candidates):
            print(f"\n=== 尝试 {i + 1}/{len(password_candidates)} ===")

            if self.test_login_with_password_format(password_format, key):
                print("登录成功！")
                return True

            # 添加延迟，模拟人类操作
            if i < len(password_candidates) - 1:
                self.human_like_delay(2, 4)

        print("所有尝试都失败了")
        return False

    def manual_analysis_mode(self):
        """
        手动分析模式

        当自动登录失败时，提供手动分析功能
        """
        print("\n=== 手动分析模式 ===")
        print("请手动在浏览器中登录，并观察网络请求...")
        print("1. 打开开发者工具 (F12)")
        print("2. 切换到 Network 标签")
        print("3. 在网站上进行登录操作")
        print("4. 观察登录请求的参数和响应")

        # 获取登录页面
        login_page_url = urljoin(self.base_url, "/index/login/index.html")
        response = self.session.get(login_page_url)

        # 保存页面内容供分析
        with open("login_page.html", "w", encoding="utf-8") as f:
            f.write(response.text)
        print("登录页面已保存到 login_page.html")

        return response.text

    # ============================================================================
    # 状态检查方法
    # ============================================================================

    def check_login_status(self):
        """
        检查当前登录状态

        访问主页检查是否已登录

        Returns:
            bool: 是否已登录
        """
        try:
            main_url = urljoin(self.base_url, "/index/index/index.html")
            response = self.session.get(main_url)

            print(f"主页访问状态: {response.status_code}")

            # 检查是否需要登录
            if "登录" in response.text or "login" in response.text.lower():
                print("未登录状态")
                return False
            else:
                print("已登录状态")
                return True

        except Exception as e:
            print(f"检查登录状态出错: {e}")
            return False

    def get_user_info(self):
        """
        获取当前用户信息

        Returns:
            str: 用户页面内容，未找到返回None
        """
        try:
            info_urls = [
                "/index/user/index.html",
                "/index/profile/index.html",
                "/index/index/user.html",
            ]

            for url in info_urls:
                full_url = urljoin(self.base_url, url)
                response = self.session.get(full_url, timeout=10)

                if response.status_code == 200:
                    print(f"成功访问: {url}")

                    # 尝试提取用户名
                    username_match = re.search(r"用户名[：:]\s*(\w+)", response.text)
                    if username_match:
                        print(f"用户名: {username_match.group(1)}")

                    # 尝试提取姓名
                    name_match = re.search(r"姓名[：:]\s*([^\s<>\]]+)", response.text)
                    if name_match:
                        print(f"姓名: {name_match.group(1)}")

                    return response.text

            print("未找到用户信息页面")
            return None

        except Exception as e:
            print(f"获取用户信息出错: {e}")
            return None

    # ============================================================================
    # 主运行方法
    # ============================================================================

    def run(self):
        """
        运行自动登录流程

        执行步骤：
        1. 检查当前登录状态
        2. 如果未登录，尝试自动登录
        3. 自动登录失败则进入手动分析模式
        """
        print("三三制微教学平台自动登录工具")
        print("=" * 50)
        print(f"目标网址: {self.base_url}")
        print(f"用户名: {self.username}")
        print(f"密码: {'*' * len(self.password)}")
        print("=" * 50)

        try:
            # 1. 检查登录状态
            print("检查当前登录状态...")
            if self.check_login_status():
                print("已经登录！")
                self.get_user_info()
                return True

            # 2. 自动登录
            print("开始自动登录...")
            if self.analyze_network_request():
                print("自动登录成功！")

                # 验证登录状态
                if self.check_login_status():
                    print("登录状态确认成功！")
                    self.get_user_info()
                else:
                    print("登录状态确认失败")

                return True

            # 3. 手动分析
            print("\n自动分析失败，进入手动分析模式...")
            self.manual_analysis_mode()

            return False

        except KeyboardInterrupt:
            print("\n用户中断操作")
            return False
        except Exception as e:
            print(f"运行时出错: {e}")
            return False


def main():
    """主函数"""
    bot = SanSanZhiAutoLogin()
    success = bot.run()

    if success:
        print("\n登录流程完成！")
    else:
        print("\n登录失败，请查看详细日志并调整策略。")


if __name__ == "__main__":
    main()
