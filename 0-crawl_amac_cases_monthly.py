"""
AMAC 纪律处分公告爬虫 - 月度版本
功能：自动爬取上一个月的纪律处分PDF公告
优化：使用异步IO提升性能，添加进度显示
"""

import asyncio
import aiohttp
from aiohttp import TCPConnector, ClientSession
from bs4 import BeautifulSoup
import os
import re
from urllib.parse import urljoin
from datetime import datetime, timedelta
from pathlib import Path
import logging
import sys
from typing import Optional, Tuple, List, NamedTuple
from dataclasses import dataclass
from tqdm import tqdm
import backoff

CATEGORIES = {
    "Institution": {
        "name": "受处分机构",
        "url": "https://www.amac.org.cn/zlgl/jlcf/scfjg/",
        "dir_prefix": "AMAC_Cases_Inst"
    },
    "Personnel": {
        "name": "受处分人员",
        "url": "https://www.amac.org.cn/zlgl/jlcf/scfry/",
        "dir_prefix": "AMAC_Cases_Person"
    }
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

MAX_GLOBAL_RETRIES = 10
GLOBAL_RETRY_DELAY = 5.0

TIMEOUT = 60
MAX_RETRIES = 5
CONCURRENT_LIMIT = 2
DELAY_BETWEEN_PAGES = 1.0
DELAY_BETWEEN_DOWNLOADS = 0.5

def setup_logging() -> logging.Logger:
    """配置结构化日志"""
    logger = logging.getLogger("amac_crawler_monthly")
    logger.setLevel(logging.INFO)
    
    if logger.handlers:
        return logger
    
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_format = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S"
    )
    console_handler.setFormatter(console_format)
    logger.addHandler(console_handler)
    
    return logger

logger = setup_logging()

@dataclass(frozen=True)
class CategoryInfo:
    """分类信息"""
    name: str
    url: str
    dir_prefix: str

class DownloadTask(NamedTuple):
    """下载任务"""
    pdf_url: str
    raw_title: str
    download_dir: Path
    item_date: datetime.date

class DownloadResult(NamedTuple):
    """下载结果"""
    success: bool
    filename: str
    message: str

def get_script_directory() -> Path:
    """获取脚本所在的绝对路径"""
    try:
        return Path(__file__).parent.resolve()
    except NameError:
        return Path.cwd().resolve()

def get_last_month_range() -> Tuple[datetime.date, datetime.date]:
    """计算上一个月的起止日期"""
    today = datetime.now()
    first_of_this_month = today.replace(day=1)
    last_of_last_month = first_of_this_month - timedelta(days=1)
    first_of_last_month = last_of_last_month.replace(day=1)
    
    logger.info(f"目标月份: {first_of_last_month.year}年{first_of_last_month.month}月, 范围: {first_of_last_month.date()} 至 {last_of_last_month.date()}")
    return first_of_last_month.date(), last_of_last_month.date()

def sanitize_filename(name: str) -> str:
    """清理文件名中的非法字符"""
    return re.sub(r'[\\/*?:"<>|]', "", name).strip()

def extract_id_from_url(url: str) -> str:
    """从 URL 中提取 ID"""
    match = re.search(r'(P\d{20,})', url)
    return match.group(1) if match else ""

def parse_date_from_text(text: str) -> Optional[datetime.date]:
    """从文本中提取日期对象"""
    match = re.search(r'(\d{4})\s*-\s*(\d{2})\s*-\s*(\d{2})', text)
    if match:
        return datetime(
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3))
        ).date()
    return None

def generate_filename(pdf_url: str, raw_title: str) -> str:
    """生成最终文件名"""
    pdf_id = extract_id_from_url(pdf_url)
    clean_title = re.sub(r'^\d{4}\s*-\s*\d{2}\s*-\s*\d{2}\s*', '', raw_title)
    clean_title = sanitize_filename(clean_title)
    
    if pdf_id:
        return f"{pdf_id}_{clean_title}.pdf"
    return f"{clean_title}.pdf"

class AsyncAMACCrawler:
    """异步AMAC爬虫"""
    
    def __init__(self):
        self.semaphore = asyncio.Semaphore(CONCURRENT_LIMIT)
        self.session: Optional[ClientSession] = None
        
    async def __aenter__(self):
        connector = TCPConnector(
            limit=CONCURRENT_LIMIT,
            limit_per_host=CONCURRENT_LIMIT,
            ttl_dns_cache=300
        )
        
        timeout = aiohttp.ClientTimeout(total=TIMEOUT)
        self.session = ClientSession(
            headers=HEADERS,
            connector=connector,
            timeout=timeout
        )
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()
    
    @backoff.on_exception(
        backoff.expo,
        (aiohttp.ClientError, asyncio.TimeoutError),
        max_tries=MAX_RETRIES,
        max_time=60
    )
    async def fetch_page(self, url: str) -> str:
        """获取页面内容"""
        async with self.semaphore:
            async with self.session.get(url) as response:
                response.raise_for_status()
                content = await response.read()
                try:
                    return content.decode('gbk')
                except UnicodeDecodeError:
                    try:
                        return content.decode('gb2312')
                    except UnicodeDecodeError:
                        return content.decode('utf-8', errors='ignore')
    
    async def download_file(self, task: DownloadTask) -> DownloadResult:
        """下载文件（原子性写入）"""
        final_filename = generate_filename(task.pdf_url, task.raw_title)
        save_path = task.download_dir / final_filename
        
        if save_path.exists():
            return DownloadResult(True, final_filename, "已存在，跳过")
        
        temp_path = save_path.with_suffix('.tmp')
        
        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                await asyncio.sleep(DELAY_BETWEEN_DOWNLOADS)
                
                async with self.semaphore:
                    async with self.session.get(task.pdf_url) as response:
                        if response.status == 404:
                            if attempt < MAX_RETRIES - 1:
                                logger.warning(f"收到404，重试 {attempt + 1}/{MAX_RETRIES}: {final_filename}")
                                await asyncio.sleep(2 ** attempt)
                                continue
                            return DownloadResult(False, final_filename, f"服务器404: 文件不存在")
                        
                        response.raise_for_status()
                        
                        content = await response.read()
                        
                        if len(content) < 100 or content[:4] != b'%PDF':
                            raise ValueError("下载内容不是有效的PDF文件")
                        
                        with open(temp_path, 'wb') as f:
                            f.write(content)
                
                temp_path.rename(save_path)
                return DownloadResult(True, final_filename, "下载成功")
                
            except aiohttp.ClientPayloadError as e:
                last_error = e
                logger.warning(f"下载中断，重试 {attempt + 1}/{MAX_RETRIES}: {final_filename} - {e}")
            except aiohttp.ClientResponseError as e:
                if e.status == 404:
                    if attempt < MAX_RETRIES - 1:
                        logger.warning(f"收到404，重试 {attempt + 1}/{MAX_RETRIES}: {final_filename}")
                        await asyncio.sleep(2 ** attempt)
                        continue
                    return DownloadResult(False, final_filename, f"服务器404: 文件不存在")
                last_error = e
                logger.warning(f"下载重试 {attempt + 1}/{MAX_RETRIES}: {final_filename} - {e}")
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last_error = e
                logger.warning(f"下载重试 {attempt + 1}/{MAX_RETRIES}: {final_filename} - {e}")
            except ValueError as e:
                last_error = e
                logger.warning(f"文件验证失败，重试 {attempt + 1}/{MAX_RETRIES}: {final_filename} - {e}")
            except Exception as e:
                last_error = e
                logger.error(f"下载异常: {final_filename} - {type(e).__name__}: {e}")
                break
            
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(2 ** attempt)
        
        if temp_path.exists():
            temp_path.unlink()
        return DownloadResult(False, final_filename, f"错误: {last_error}")
    
    async def get_pdf_from_detail(self, detail_url: str) -> Optional[str]:
        """进入详情页获取 PDF 地址"""
        try:
            html = await self.fetch_page(detail_url)
            soup = BeautifulSoup(html, 'html.parser')
            
            for link in soup.find_all('a', href=True):
                href = link['href']
                if '.pdf' in href.lower():
                    return urljoin(detail_url, href)
            
            iframe = soup.find('iframe', src=True)
            if iframe and '.pdf' in iframe['src']:
                return urljoin(detail_url, iframe['src'])
            
            return None
        except Exception as e:
            logger.warning(f"获取详情页PDF失败: {detail_url} - {e}")
            return None
    
    async def collect_download_tasks(
        self,
        category_info: CategoryInfo,
        target_start_date: datetime.date,
        target_end_date: datetime.date,
        download_dir: Path
    ) -> List[DownloadTask]:
        """收集所有需要下载的任务"""
        base_url = category_info.url
        tasks: List[DownloadTask] = []
        
        page_index = 0
        should_stop = False
        
        with tqdm(desc=f"扫描页面 [{category_info.name}]", unit="页") as pbar:
            while not should_stop:
                url = base_url + ("index.html" if page_index == 0 else f"index_{page_index}.html")
                
                try:
                    html = await self.fetch_page(url)
                    soup = BeautifulSoup(html, 'html.parser')
                    
                    selectors = [
                        'ul.news_list li',
                        '.list-main li',
                        '.yl-list-con li',
                        'ul.txt-list li',
                        '.list ul li',
                        '.content ul li'
                    ]
                    
                    items = []
                    for selector in selectors:
                        found = soup.select(selector)
                        if found:
                            items = found
                            break
                    
                    if not items:
                        main_area = soup.find('div', class_=re.compile('right|main'))
                        if main_area:
                            potential_items = main_area.find_all('li')
                            items = [li for li in potential_items if len(li.get_text(strip=True)) > 10]
                    
                    if not items:
                        logger.info(f"第 {page_index + 1} 页未找到列表结构，停止翻页")
                        break
                    
                    page_task_count = 0
                    
                    for item in items:
                        link_tag = item.find('a', href=True)
                        if not link_tag:
                            continue
                        
                        full_row_text = item.get_text(strip=True)
                        raw_title = link_tag.get_text(strip=True)
                        link_url = link_tag['href']
                        
                        item_date = parse_date_from_text(full_row_text)
                        if not item_date:
                            continue
                        
                        if item_date > target_end_date:
                            continue
                        elif item_date < target_start_date:
                            logger.info(f"遇到过期日期 {item_date}，停止翻页")
                            should_stop = True
                            break
                        
                        full_link = urljoin(url, link_url)
                        
                        if '.pdf' in full_link.lower():
                            pdf_url = full_link
                        else:
                            pdf_url = await self.get_pdf_from_detail(full_link)
                        
                        if pdf_url:
                            tasks.append(DownloadTask(pdf_url, raw_title, download_dir, item_date))
                            page_task_count += 1
                    
                    pbar.set_postfix({"任务": len(tasks)})
                    pbar.update(1)
                    
                    page_index += 1
                    await asyncio.sleep(DELAY_BETWEEN_PAGES)
                    
                except aiohttp.ClientResponseError as e:
                    if e.status == 404:
                        logger.info(f"第 {page_index + 1} 页不存在 (404)，停止翻页")
                        break
                    logger.error(f"处理页面出错: {e}")
                    break
                except Exception as e:
                    logger.error(f"处理页面出错: {e}")
                    break
        
        logger.info(f"{category_info.name}: 收集到 {len(tasks)} 个下载任务")
        return tasks
    
    async def execute_downloads(
        self,
        tasks: List[DownloadTask],
        category_name: str
    ) -> Tuple[int, int]:
        """执行下载任务，失败后全局重试"""
        if not tasks:
            logger.info(f"{category_name}: 无需要下载的文件")
            return 0, 0
        
        logger.info(f"{category_name}: 开始下载 {len(tasks)} 个文件")
        
        total_success = 0
        total_skip = 0
        pending_tasks = list(tasks)
        
        for retry_round in range(MAX_GLOBAL_RETRIES + 1):
            if not pending_tasks:
                break
            
            if retry_round > 0:
                logger.info(f"{category_name}: 第 {retry_round} 轮全局重试，剩余 {len(pending_tasks)} 个文件")
                await asyncio.sleep(GLOBAL_RETRY_DELAY)
            
            results = []
            download_coros = [self.download_file(task) for task in pending_tasks]
            
            with tqdm(total=len(pending_tasks), desc=f"下载文件 [{category_name}]", unit="文件", leave=False) as pbar:
                for coro in asyncio.as_completed(download_coros):
                    result = await coro
                    results.append(result)
                    pbar.update(1)
            
            failed_tasks = []
            for i, result in enumerate(results):
                if "跳过" in result.message:
                    total_skip += 1
                elif result.success:
                    total_success += 1
                else:
                    failed_tasks.append(pending_tasks[i])
                    logger.error(f"下载失败: {result.filename} - {result.message}")
            
            pending_tasks = failed_tasks
            
            if not failed_tasks:
                break
        
        total_fail = len(pending_tasks)
        if total_fail > 0:
            logger.warning(f"{category_name}: 最终失败 {total_fail} 个文件")
            for task in pending_tasks:
                logger.warning(f"  - {task.pdf_url}")
        
        logger.info(f"{category_name}: 下载完成 - 成功 {total_success}, 跳过 {total_skip}, 失败 {total_fail}")
        return total_success, total_fail

async def crawl_category(
    crawler: AsyncAMACCrawler,
    category_info: CategoryInfo,
    target_start_date: datetime.date,
    target_end_date: datetime.date,
    download_dir: Path
) -> Tuple[int, int]:
    """爬取指定分类"""
    logger.info(f"{'='*20} 开始爬取: {category_info.name} {'='*20}")
    logger.info(f"保存路径: {download_dir}")
    
    download_dir.mkdir(parents=True, exist_ok=True)
    
    tasks = await crawler.collect_download_tasks(
        category_info, target_start_date, target_end_date, download_dir
    )
    
    return await crawler.execute_downloads(tasks, category_info.name)

async def main() -> None:
    """主函数"""
    start_time = datetime.now()
    
    try:
        script_dir = get_script_directory()
        start_date, end_date = get_last_month_range()
        
        start_str = start_date.strftime("%Y%m%d")
        end_str = end_date.strftime("%Y%m%d")
        
        total_success = 0
        total_fail = 0
        
        async with AsyncAMACCrawler() as crawler:
            for key, config in CATEGORIES.items():
                category_info = CategoryInfo(
                    name=config['name'],
                    url=config['url'],
                    dir_prefix=config['dir_prefix']
                )
                
                base_dir = script_dir / "AMAC_Discipline_PDFs"
                sub_folder = f"{category_info.dir_prefix}_{start_str}_{end_str}"
                full_download_path = base_dir / sub_folder
                
                success, fail = await crawl_category(
                    crawler, category_info, start_date, end_date, full_download_path
                )
                total_success += success
                total_fail += fail
        
        elapsed = (datetime.now() - start_time).total_seconds()
        logger.info(f"\n全部完成: 成功 {total_success}, 失败 {total_fail}, 耗时 {elapsed:.1f}秒")
        
    except KeyboardInterrupt:
        logger.info("\n用户中断，正在退出...")
    except Exception as e:
        logger.error(f"程序异常: {e}")
        raise

if __name__ == "__main__":
    asyncio.run(main())
