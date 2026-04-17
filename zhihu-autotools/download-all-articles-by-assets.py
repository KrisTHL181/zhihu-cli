#!/usr/bin/env python3
"""
全自动下载所有文章的 Pipeline
自动读取 all_assets_list.json 中的文章列表并批量下载
"""

import json
import os
import sys
import time
import re
from typing import List, Dict, Any
from datetime import datetime
from curl_cffi import requests

# 导入已有的模块
from html2markdown import PageToMarkdown
from download_contents import extract_config_from_curl, extract_metadata_from_html, sanitize_filename


class ArticleDownloadPipeline:
    """全自动文章下载流水线"""
    
    def __init__(self, assets_file: str = "all_assets_list.json", output_dir: str = "./downloads/articles"):
        """
        初始化下载流水线
        
        Args:
            assets_file: 资产列表文件路径
            output_dir: 输出目录
        """
        self.assets_file = assets_file
        self.output_dir = output_dir
        self.articles = []  # 文章列表
        self.session = requests.Session()
        self.headers = {}
        self.md_converter = PageToMarkdown(skip_empty=True)
        
        # 统计信息
        self.stats = {
            'total': 0,
            'success': 0,
            'failed': 0,
            'skipped': 0,
            'failed_urls': []
        }
        
        # 创建输出目录
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)
    
    def load_articles(self) -> bool:
        """
        从 JSON 文件中加载所有 article 类型的资产
        
        Returns:
            是否成功加载
        """
        if not os.path.exists(self.assets_file):
            print(f"[Error] 文件不存在: {self.assets_file}")
            return False
        
        try:
            with open(self.assets_file, 'r', encoding='utf-8') as f:
                all_assets = json.load(f)
        except json.JSONDecodeError as e:
            print(f"[Error] JSON 解析失败: {e}")
            return False
        
        # 筛选出 article 类型
        self.articles = [asset for asset in all_assets if asset.get('type') == 'article']
        self.stats['total'] = len(self.articles)
        
        print(f"[Info] 共找到 {len(self.articles)} 篇文章")
        
        if not self.articles:
            print("[Warning] 没有找到任何文章")
            return False
        
        return True
    
    def load_headers_from_curl(self) -> bool:
        """
        从用户粘贴的 cURL 命令中加载请求头
        
        Returns:
            是否成功加载
        """
        print("\n--- 请粘贴【任意知乎文章页面】的 cURL 命令 ---")
        print("(在浏览器开发者工具中复制为 cURL 格式)")
        print("提示：按 Ctrl+D (Linux/Mac) 或 Ctrl+Z+Enter (Windows) 结束输入\n")
        
        curl_input = sys.stdin.read()
        
        if not curl_input.strip():
            print("[Error] 未输入有效的 cURL 命令")
            return False
        
        url, headers = extract_config_from_curl(curl_input)
        
        if not headers:
            print("[Error] 无法从 cURL 中提取请求头")
            return False
        
        # 移除可能导致问题的头部
        headers.pop('Accept-Encoding', None)
        
        self.headers = headers
        print(f"[Success] 已加载 {len(headers)} 个请求头")
        print(f"[Info] 请求来源: {url}\n")
        return True
    
    def build_article_url(self, article_id: str) -> str:
        """
        根据文章 ID 构建文章 URL
        
        Args:
            article_id: 文章 ID
            
        Returns:
            完整的文章 URL
        """
        return f"https://zhuanlan.zhihu.com/p/{article_id}"
    
    def download_article(self, article: Dict[str, Any], index: int) -> bool:
        """
        下载单篇文章并转换为 Markdown
        
        Args:
            article: 文章信息字典
            index: 当前索引
            
        Returns:
            是否下载成功
        """
        article_id = article.get('id')
        title = article.get('title', f'article_{article_id}')
        url = self.build_article_url(article_id)
        
        print(f"\n[{index + 1}/{self.stats['total']}] 下载: {title}")
        print(f"  URL: {url}")
        
        try:
            # 发送请求
            resp = self.session.get(
                url,
                headers=self.headers,
                impersonate="chrome110",
                timeout=30
            )
            resp.raise_for_status()
            
            html_content = resp.text
            
            # 提取元数据
            metadata = extract_metadata_from_html(html_content)
            
            # 使用已有的标题（如果从 HTML 提取失败）
            if metadata['title'] == 'untitled':
                metadata['title'] = title
            
            # 转换为 Markdown
            markdown_content = self.md_converter.convert(html_content, url)
            
            # 生成文件名
            safe_title = sanitize_filename(metadata['title'])
            safe_author = sanitize_filename(metadata['author'])
            safe_created = metadata['created'].replace(':', '-') if metadata['created'] else "UNKNOWN"
            
            filename = f"{safe_created}_{safe_author}_{safe_title}.md"
            # 限制文件名长度
            if len(filename) > 200:
                # 保留日期和作者，截断标题
                filename = f"{safe_created}_{safe_author}_{safe_title[:150]}.md"
            
            filepath = os.path.join(self.output_dir, filename)
            
            # 处理文件名重复
            if os.path.exists(filepath):
                base, ext = os.path.splitext(filepath)
                counter = 1
                while os.path.exists(f"{base}_{counter}{ext}"):
                    counter += 1
                filepath = f"{base}_{counter}{ext}"
            
            # 添加 Markdown 头信息
            final_content = f"""---
title: {metadata['title']}
author: {metadata['author']}
created: {metadata['created']}
source: {url}
id: {article_id}
---

{markdown_content}
"""
            
            # 保存文件
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(final_content)
            
            print(f"  [Success] 保存至: {os.path.basename(filepath)}")
            print(f"  标题: {metadata['title']}")
            print(f"  作者: {metadata['author']}")
            print(f"  时间: {metadata['created']}")
            
            return True
            
        except requests.exceptions.Timeout:
            print(f"  [Error] 请求超时")
            self.stats['failed_urls'].append({'id': article_id, 'title': title, 'error': 'timeout'})
            return False
        except requests.exceptions.HTTPError as e:
            print(f"  [Error] HTTP 错误: {e}")
            self.stats['failed_urls'].append({'id': article_id, 'title': title, 'error': str(e)})
            return False
        except Exception as e:
            print(f"  [Error] 未知错误: {e}")
            self.stats['failed_urls'].append({'id': article_id, 'title': title, 'error': str(e)})

            return False
    
    def run(self, delay: float = 1.5, resume_from: int = 0) -> None:
        """
        运行下载流水线
        
        Args:
            delay: 请求间隔（秒）
            resume_from: 从第几个文章开始（断点续传）
        """
        print("=" * 60)
        print("知乎文章全自动下载流水线")
        print("=" * 60)
        
        # 1. 加载文章列表
        if not self.load_articles():
            return
        
        # 2. 加载请求头
        if not self.load_headers_from_curl():
            return
        
        # 3. 确认开始下载
        print(f"\n准备下载 {self.stats['total']} 篇文章")
        print(f"输出目录: {self.output_dir}")
        print(f"请求间隔: {delay} 秒")
        
        if resume_from > 0:
            print(f"从第 {resume_from + 1} 篇文章开始")
        
        confirm = input("\n是否开始下载？(y/n): ").strip().lower()
        if confirm != 'y':
            print("已取消下载")
            return
        
        # 4. 开始下载
        print("\n" + "=" * 60)
        print("开始下载...")
        print("=" * 60)
        
        start_time = time.time()
        
        for i, article in enumerate(self.articles[resume_from:], start=resume_from):
            success = self.download_article(article, i)
            
            if success:
                self.stats['success'] += 1
            else:
                self.stats['failed'] += 1
            
            # 请求间隔
            if i < self.stats['total'] - 1:
                time.sleep(delay)
        
        elapsed = time.time() - start_time
        
        # 5. 输出统计报告
        self._print_report(elapsed)
        
        # 6. 保存失败记录
        if self.stats['failed_urls']:
            self._save_failed_log()
    
    def _print_report(self, elapsed: float) -> None:
        """打印下载报告"""
        print("\n" + "=" * 60)
        print("下载完成！")
        print("=" * 60)
        print(f"总计文章: {self.stats['total']}")
        print(f"成功下载: {self.stats['success']}")
        print(f"下载失败: {self.stats['failed']}")
        print(f"总耗时: {elapsed:.2f} 秒")
        
        if self.stats['success'] > 0:
            avg_time = elapsed / self.stats['success']
            print(f"平均每篇: {avg_time:.2f} 秒")
        
        print(f"输出目录: {self.output_dir}")
        
        if self.stats['failed'] > 0:
            print(f"\n失败列表 ({len(self.stats['failed_urls'])} 篇):")
            for item in self.stats['failed_urls']:
                print(f"  - {item['title']}: {item['error']}")
    
    def _save_failed_log(self) -> None:
        """保存失败记录到文件"""
        log_file = os.path.join(self.output_dir, f"failed_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        with open(log_file, 'w', encoding='utf-8') as f:
            json.dump(self.stats['failed_urls'], f, ensure_ascii=False, indent=2)
        print(f"\n失败记录已保存: {log_file}")


def load_saved_headers(headers_file: str = "headers.json") -> Dict[str, str]:
    """
    从文件加载已保存的请求头
    
    Args:
        headers_file: 请求头文件路径
        
    Returns:
        请求头字典
    """
    if os.path.exists(headers_file):
        try:
            with open(headers_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return {}


def save_headers(headers: Dict[str, str], headers_file: str = "headers.json") -> None:
    """
    保存请求头到文件
    
    Args:
        headers: 请求头字典
        headers_file: 保存路径
    """
    # 移除可能敏感的信息
    safe_headers = {k: v for k, v in headers.items() 
                    if k.lower() not in ['cookie', 'authorization', 'x-zse-93']}
    safe_headers['_note'] = 'Cookie and auth headers removed for security'
    
    with open(headers_file, 'w', encoding='utf-8') as f:
        json.dump(safe_headers, f, ensure_ascii=False, indent=2)


class QuickDownloadPipeline(ArticleDownloadPipeline):
    """快速下载流水线 - 简化版，直接使用已有 headers"""
    
    def __init__(self, assets_file: str = "all_assets_list.json", output_dir: str = "./downloads/articles"):
        super().__init__(assets_file, output_dir)
        self.headers_loaded = False
    
    def load_headers_from_file(self, headers_file: str = "headers.json") -> bool:
        """
        从文件加载已保存的请求头
        
        Args:
            headers_file: 请求头文件路径
            
        Returns:
            是否成功加载
        """
        headers = load_saved_headers(headers_file)
        if headers:
            self.headers = headers
            self.headers_loaded = True
            print(f"[Success] 从文件加载了 {len(headers)} 个请求头")
            return True
        return False
    
    def load_headers_from_curl(self) -> bool:
        """交互式加载请求头，并可选保存"""
        if not super().load_headers_from_curl():
            return False
        
        # 询问是否保存
        save = input("\n是否保存请求头以便下次使用？(y/n): ").strip().lower()
        if save == 'y':
            save_headers(self.headers)
            print("[Info] 请求头已保存到 headers.json")
        
        return True
    
    def run_auto(self, delay: float = 1.5, resume_from: int = 0) -> None:
        """
        自动运行流水线，优先使用已保存的请求头
        
        Args:
            delay: 请求间隔
            resume_from: 断点续传位置
        """
        print("=" * 60)
        print("知乎文章快速下载流水线")
        print("=" * 60)
        
        if not self.load_articles():
            return
        
        # 尝试从文件加载请求头
        if not self.load_headers_from_file():
            print("[Info] 未找到已保存的请求头，需要手动输入")
            if not self.load_headers_from_curl():
                return
        
        # 继续执行下载
        self.run(delay, resume_from)


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description="全自动下载 all_assets_list.json 中的所有文章")
    parser.add_argument('--assets-file', '-a', default='all_assets_list.json',
                        help='资产列表文件路径 (默认: all_assets_list.json)')
    parser.add_argument('--output-dir', '-o', default='./downloads/articles',
                        help='输出目录 (默认: ./downloads/articles)')
    parser.add_argument('--delay', '-d', type=float, default=1.5,
                        help='请求间隔秒数 (默认: 1.5)')
    parser.add_argument('--resume', '-r', type=int, default=0,
                        help='从第几个文章开始下载 (默认: 0)')
    parser.add_argument('--quick', '-q', action='store_true',
                        help='快速模式，自动使用已保存的请求头')
    
    args = parser.parse_args()
    
    if args.quick:
        pipeline = QuickDownloadPipeline(
            assets_file=args.assets_file,
            output_dir=args.output_dir
        )
        pipeline.run_auto(delay=args.delay, resume_from=args.resume)
    else:
        pipeline = ArticleDownloadPipeline(
            assets_file=args.assets_file,
            output_dir=args.output_dir
        )
        pipeline.run(delay=args.delay, resume_from=args.resume)


if __name__ == "__main__":
    main()
