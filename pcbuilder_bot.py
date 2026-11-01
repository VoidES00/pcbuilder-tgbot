import asyncio
import html
import json
import logging
import os
import random
import re
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote_plus, urlparse

import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from dotenv import load_dotenv

load_dotenv()
for _k, _v in {
    "LIVE_PRICES": "1",
    "USE_BROWSER": "1",
    "BROWSER_CHANNEL": "msedge" if sys.platform == "win32" else "",
    "AVITO_DELAY_MIN": "25",
    "AVITO_DELAY_MAX": "50",
    "DNS_ENABLED": "0",
}.items():
    os.environ.setdefault(_k, _v)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("pcbuilder")

BASE_DIR = Path(__file__).resolve().parent
CACHE_FILE = BASE_DIR / "prices_cache.json"
TTL = 48 * 3600
MIN_BUDGET = 50_000
MAX_BUDGET = 1_000_000
NEW_ONLY = {"ssd", "psu"}
UA_OS = "Windows NT 10.0; Win64; x64" if sys.platform == "win32" else "X11; Linux x86_64"
HEADERS = {
    "User-Agent": f"Mozilla/5.0 ({UA_OS}) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Accept": "text/html,application/xhtml+xml",
}
DNS_WAIT = ".product-buy__price"
AVITO_WAIT = '[data-marker="item-price"], [itemprop="price"]'

PURPOSES = {
    "game": "🎮 Игры",
    "work": "💼 Работа",
    "balance": "⚖️ Баланс",
    "llm": "🧠 LLM / нейросети",
}
KIND_RU = {
    "cpu": "Процессор", "cooler": "Кулер", "mb": "Материнская плата", "ram": "Память",
    "gpu": "Видеокарта", "ssd": "SSD", "psu": "Блок питания", "case": "Корпус",
}
ORDER = ["cpu", "cooler", "mb", "ram", "gpu", "ssd", "psu", "case"]


@dataclass(frozen=True)
class Part:
    id: str
    kind: str
    name: str
    new: int | None
    used: int | None
    attrs: dict = field(default_factory=dict, hash=False, compare=False)

    @property
    def query(self) -> str:
        return self.attrs.get("q", self.name)


PARTS: list[Part] = []


def add(id, kind, name, new, used, **attrs):
    PARTS.append(Part(id, kind, name, new, used, attrs))


for row in [
    ("r5_5500", "AMD Ryzen 5 5500", "AM4", 52, 40, 65, 0, 1, 7500, 5000),
    ("r5_5600", "AMD Ryzen 5 5600", "AM4", 58, 46, 65, 0, 1, 9500, 7000),
    ("r7_5700x", "AMD Ryzen 7 5700X", "AM4", 60, 62, 65, 1, 1, 15000, 11500),
    ("r7_5700x3d", "AMD Ryzen 7 5700X3D", "AM4", 72, 62, 105, 1, 2, 24000, 19000),
    ("r9_5900x", "AMD Ryzen 9 5900X", "AM4", 62, 85, 105, 2, 2, 24000, 16000),
    ("r5_7500f", "AMD Ryzen 5 7500F", "AM5", 70, 55, 65, 1, 1, 12500, 10500),
    ("r5_9600x", "AMD Ryzen 5 9600X", "AM5", 78, 62, 65, 1, 1, 21000, 18000),
    ("r7_7700", "AMD Ryzen 7 7700", "AM5", 76, 72, 65, 0, 1, 25000, 20000),
    ("r7_9700x", "AMD Ryzen 7 9700X", "AM5", 82, 75, 65, 1, 1, 31000, 27000),
    ("r7_7800x3d", "AMD Ryzen 7 7800X3D", "AM5", 100, 74, 120, 2, 2, 38000, 32000),
    ("r9_7900", "AMD Ryzen 9 7900", "AM5", 78, 100, 65, 0, 2, 36000, 28000),
    ("r9_9950x", "AMD Ryzen 9 9950X", "AM5", 90, 150, 170, 2, 2, 58000, 48000),
    ("i3_12100f", "Intel Core i3-12100F", "LGA1700", 50, 32, 58, 0, 1, 5500, 4500),
    ("i5_12400f", "Intel Core i5-12400F", "LGA1700", 58, 46, 65, 0, 1, 9500, 7500),
    ("i5_13400f", "Intel Core i5-13400F", "LGA1700", 64, 60, 65, 0, 1, 14000, 11000),
    ("i5_14400f", "Intel Core i5-14400F", "LGA1700", 66, 62, 65, 0, 1, 15500, 13000),
    ("i5_14600kf", "Intel Core i5-14600KF", "LGA1700", 80, 90, 125, 2, 2, 23000, 19000),
    ("i7_14700f", "Intel Core i7-14700F", "LGA1700", 84, 115, 65, 2, 2, 33000, 27000),
]:
    i, name, socket, game, prod, tdp, cooler, tier, new, used = row
    add(i, "cpu", name, new, used, socket=socket, game=game, prod=prod, tdp=tdp, cooler=cooler, tier=tier)

for row in [
    ("gtx1660s", "GeForce GTX 1660 Super 6GB", "nvidia", 24, 6, 125, None, 8500),
    ("rx6600", "Radeon RX 6600 8GB", "amd", 28, 8, 132, 19000, 12000),
    ("rtx3060", "GeForce RTX 3060 12GB", "nvidia", 30, 12, 170, 26000, 16500),
    ("arc_b580", "Intel Arc B580 12GB", "intel", 38, 12, 190, 28000, None),
    ("rtx4060", "GeForce RTX 4060 8GB", "nvidia", 38, 8, 115, 28000, 23000),
    ("rtx3060ti", "GeForce RTX 3060 Ti 8GB", "nvidia", 38, 8, 200, None, 21500),
    ("rx6700xt", "Radeon RX 6700 XT 12GB", "amd", 41, 12, 230, None, 21000),
    ("rtx5060", "GeForce RTX 5060 8GB", "nvidia", 44, 8, 145, 33000, None),
    ("rtx3070", "GeForce RTX 3070 8GB", "nvidia", 44, 8, 220, None, 24000),
    ("rtx4060ti16", "GeForce RTX 4060 Ti 16GB", "nvidia", 44, 16, 160, 40000, 33000),
    ("rx7700xt", "Radeon RX 7700 XT 12GB", "amd", 50, 12, 245, 40000, 31000),
    ("rtx5060ti16", "GeForce RTX 5060 Ti 16GB", "nvidia", 52, 16, 180, 52000, None),
    ("rtx4070", "GeForce RTX 4070 12GB", "nvidia", 55, 12, 200, 50000, 42000),
    ("rtx3080", "GeForce RTX 3080 10GB", "nvidia", 56, 10, 320, None, 32000),
    ("rx7800xt", "Radeon RX 7800 XT 16GB", "amd", 61, 16, 263, 52000, 43000),
    ("rtx4070s", "GeForce RTX 4070 Super 12GB", "nvidia", 63, 12, 220, 62000, 53000),
    ("rtx5070", "GeForce RTX 5070 12GB", "nvidia", 64, 12, 250, 62000, None),
    ("rtx3090", "GeForce RTX 3090 24GB", "nvidia", 66, 24, 350, None, 62000),
    ("rtx4070tis", "GeForce RTX 4070 Ti Super 16GB", "nvidia", 72, 16, 285, 85000, 72000),
    ("rtx5070ti", "GeForce RTX 5070 Ti 16GB", "nvidia", 80, 16, 300, 95000, None),
    ("rtx4080s", "GeForce RTX 4080 Super 16GB", "nvidia", 85, 16, 320, 115000, 98000),
    ("rtx5080", "GeForce RTX 5080 16GB", "nvidia", 92, 16, 360, 135000, None),
    ("rtx4090", "GeForce RTX 4090 24GB", "nvidia", 100, 24, 450, 230000, 175000),
]:
    i, name, vendor, perf, vram, tdp, new, used = row
    add(i, "gpu", name, new, used, vendor=vendor, perf=perf, vram=vram, tdp=tdp)

for i, name, socket, ram, tier, new, used in [
    ("am4_a520", "Gigabyte A520M K V2", "AM4", "DDR4", 1, 5500, 3500),
    ("am4_b550", "MSI B550M PRO-VDH WIFI", "AM4", "DDR4", 2, 9500, 6500),
    ("am5_a620", "ASRock A620M-HDV/M.2", "AM5", "DDR5", 1, 8500, 7000),
    ("am5_b650", "MSI PRO B650M-P", "AM5", "DDR5", 2, 13500, 10500),
    ("1700_h610", "ASRock H610M-HDV/M.2 (DDR4)", "LGA1700", "DDR4", 1, 5800, 4000),
    ("1700_b660", "MSI PRO B660M-A (DDR4)", "LGA1700", "DDR4", 2, 10500, 7500),
    ("1700_b760", "MSI PRO B760M-A WIFI (DDR5)", "LGA1700", "DDR5", 2, 13500, 10500),
]:
    add(i, "mb", name, new, used, socket=socket, ram=ram, tier=tier)

for i, name, typ, gb, new, used in [
    ("d4_16", "Kingston Fury Beast 16GB (2x8) DDR4-3200", "DDR4", 16, 4500, 3000),
    ("d4_32", "Kingston Fury Beast 32GB (2x16) DDR4-3200", "DDR4", 32, 8500, 5500),
    ("d4_64", "Kingston Fury Beast 64GB (2x32) DDR4-3200", "DDR4", 64, 17500, 11000),
    ("d5_16", "Kingston Fury Beast 16GB (2x8) DDR5-5600", "DDR5", 16, 8000, 6000),
    ("d5_32", "Kingston Fury Beast 32GB (2x16) DDR5-6000", "DDR5", 32, 16000, 12000),
    ("d5_64", "Kingston Fury Beast 64GB (2x32) DDR5-5600", "DDR5", 64, 32000, 24000),
]:
    add(i, "ram", name, new, used, type=typ, gb=gb)

for i, name, gb, new in [
    ("ssd_500", "Kingston NV3 500GB NVMe", 500, 4000),
    ("ssd_1000", "Kingston NV3 1TB NVMe", 1000, 6500),
    ("ssd_2000", "Kingston NV3 2TB NVMe", 2000, 12500),
]:
    add(i, "ssd", name, new, None, gb=gb)

for i, name, watts, new in [
    ("psu_500", "DeepCool PF500 500W", 500, 3500),
    ("psu_600", "DeepCool PF600 600W", 600, 4300),
    ("psu_650", "DeepCool PL650D 650W", 650, 6000),
    ("psu_750", "MSI MAG A750GL 750W", 750, 8500),
    ("psu_850", "MSI MAG A850GL 850W", 850, 11000),
    ("psu_1000", "MSI MAG A1000GL 1000W", 1000, 16000),
    ("psu_1250", "MSI MPG A1250G 1250W", 1250, 22000),
]:
    add(i, "psu", name, new, None, watts=watts)

for i, name, bonus, new, used in [
    ("case_1", "Zalman S2", 0, 2500, 1500),
    ("case_2", "DeepCool CC560", 2, 5000, 3000),
    ("case_3", "Lian Li Lancool 216", 4, 9500, None),
]:
    add(i, "case", name, new, used, bonus=bonus)

for i, name, tier, new, used in [
    ("cool_1", "DeepCool AK400", 1, 2400, 1500),
    ("cool_2", "Thermalright Peerless Assassin 120 SE", 2, 3500, 2500),
]:
    add(i, "cooler", name, new, used, tier=tier)

BY_KIND: dict[str, list[Part]] = {}
for _p in PARTS:
    BY_KIND.setdefault(_p.kind, []).append(_p)


def mode_for(part: Part, condition: str) -> str | None:
    if condition == "new" or part.kind in NEW_ONLY:
        return "new" if part.new is not None else None
    return "used" if part.used is not None else None


class PriceStore:
    def __init__(self, path: Path = CACHE_FILE):
        self.path = path
        try:
            self.data: dict[str, dict] = json.loads(path.read_text("utf-8"))
        except Exception:
            self.data = {}

    def save(self):
        try:
            self.path.write_text(json.dumps(self.data, ensure_ascii=False), "utf-8")
        except Exception as e:
            log.warning("не удалось сохранить кэш: %s", e)

    def set(self, part: Part, mode: str, price: int, src: str):
        self.data[f"{part.id}:{mode}"] = {"price": price, "src": src, "ts": time.time()}

    def fresh(self, part: Part, mode: str) -> bool:
        e = self.data.get(f"{part.id}:{mode}")
        return bool(e) and time.time() - e["ts"] < TTL

    def get(self, part: Part, condition: str) -> tuple[int, str] | None:
        mode = mode_for(part, condition)
        if mode is None:
            return None
        e = self.data.get(f"{part.id}:{mode}")
        if e and time.time() - e["ts"] < TTL:
            return e["price"], e["src"]
        return getattr(part, mode), "оценка"


def _ints(pattern: str, text: str) -> list[int]:
    return [int(d) for m in re.findall(pattern, text) if (d := re.sub(r"\D", "", m))]


def _sane(values: list[int], ref: int) -> list[int]:
    return [v for v in values if 0.4 * ref <= v <= 2.5 * ref]


async def fetch_dns(get, part: Part) -> int | None:
    page = await get(f"https://www.dns-shop.ru/search/?q={quote_plus(part.query)}", DNS_WAIT)
    raw = _ints(r'product-buy__price[^>]*>\s*([\d\s\u00a0]+?)\s*₽', page)[:8]
    prices = _sane(raw, part.new)
    if not prices:
        log.info("DNS: %s — после фильтра 0 цен (ожидалось ~%d ₽; выдача: %s)", part.id, part.new, raw)
    return min(prices, default=None)


async def fetch_avito(get, part: Part) -> int | None:
    region = os.getenv("AVITO_REGION", "rossiya")
    page = await get(f"https://www.avito.ru/{region}/tovary_dlya_kompyutera?q={quote_plus(part.query)}", AVITO_WAIT)
    raw = (_ints(r'itemprop="price"[^>]*content="(\d+)"', page)
           or _ints(r'content="(\d+)"[^>]*itemprop="price"', page))[:30]
    prices = sorted(_sane(raw, part.used))
    if len(prices) < 3:
        log.info("Авито: %s — после фильтра %d цен (ожидалось ~%d ₽; выдача: %s)",
                 part.id, len(prices), part.used, sorted(raw)[:8])
        return None
    return int(statistics.median(prices[: max(3, int(len(prices) * 0.6))]))


class HttpGetter:
    def __init__(self, proxy_url: str | None):
        self.proxy_url = proxy_url
        self.session: aiohttp.ClientSession | None = None

    async def start(self):
        connector = None
        if self.proxy_url and self.proxy_url.lower().startswith("socks"):
            from aiohttp_socks import ProxyConnector
            connector = ProxyConnector.from_url(self.proxy_url, rdns=True)
        self.session = aiohttp.ClientSession(connector=connector)

    async def __call__(self, url: str, wait_for: str) -> str:
        proxy = self.proxy_url if self.proxy_url and self.proxy_url.startswith("http") else None
        async with self.session.get(url, headers=HEADERS, proxy=proxy,
                                    timeout=aiohttp.ClientTimeout(total=20)) as r:
            if r.status != 200:
                raise RuntimeError(f"HTTP {r.status}")
            return await r.text()

    async def close(self):
        if self.session:
            await self.session.close()


class BrowserGetter:
    def __init__(self, proxy_url: str | None):
        self.proxy_url = proxy_url
        self.pw = self.ctx = None

    def _proxy(self):
        if not self.proxy_url:
            return None
        u = urlparse(self.proxy_url)
        p = {"server": f"{u.scheme}://{u.hostname}:{u.port}"}
        if u.username:
            p["username"], p["password"] = u.username, u.password or ""
        return p

    async def start(self):
        from playwright.async_api import async_playwright
        self.pw = await async_playwright().start()
        self.ctx = await self.pw.chromium.launch_persistent_context(
            str(BASE_DIR / "browser_profile"),
            headless=os.getenv("BROWSER_HEADLESS", "1") == "1",
            locale="ru-RU", timezone_id="Asia/Novosibirsk",
            viewport={"width": 1366, "height": 800},
            user_agent=HEADERS["User-Agent"],
            proxy=self._proxy(),
            channel=os.getenv("BROWSER_CHANNEL") or None,
            args=["--disable-blink-features=AutomationControlled"],
        )

    async def __call__(self, url: str, wait_for: str) -> str:
        page = await self.ctx.new_page()
        try:
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=40_000)
            try:
                await page.wait_for_selector(wait_for, state="attached", timeout=25_000)
            except Exception:
                raise RuntimeError(f"нет цен на странице (HTTP {resp.status if resp else '?'}, "
                                   f"title={await page.title()!r})")
            return await page.content()
        finally:
            await page.close()

    async def close(self):
        if self.ctx:
            await self.ctx.close()
        if self.pw:
            await self.pw.stop()


class PriceUpdater:
    SOURCES = {"DNS": ("new", fetch_dns), "Авито": ("used", fetch_avito)}

    def __init__(self, store: PriceStore):
        self.store = store
        self.proxy_url = os.getenv("PROXY_URL") or None
        self.pause_until = dict.fromkeys(self.SOURCES, 0.0)
        self.fails = dict.fromkeys(self.SOURCES, 0)
        self.backoff = dict.fromkeys(self.SOURCES, 1800)
        self.logged_pause: dict[str, float] = {}
        self.delay = {"DNS": (4, 8),
                      "Авито": (int(os.getenv("AVITO_DELAY_MIN")), int(os.getenv("AVITO_DELAY_MAX")))}

    async def _one(self, get, src: str, part: Part):
        mode, fn = self.SOURCES[src]
        now = time.time()
        if now < self.pause_until[src]:
            if self.logged_pause.get(src) != self.pause_until[src]:
                log.info("%s: пауза ещё ~%d мин, детали пропускаются", src, int(self.pause_until[src] - now) // 60)
                self.logged_pause[src] = self.pause_until[src]
            return
        if self.store.fresh(part, mode):
            return
        try:
            price = await fn(get, part)
        except Exception as e:
            self.fails[src] += 1
            log.warning("%s: %s (%s)", src, part.id, e)
            if self.fails[src] >= 2:
                self.pause_until[src] = time.time() + self.backoff[src]
                log.warning("%s: блокировка, пауза %d мин", src, self.backoff[src] // 60)
                self.backoff[src] = min(self.backoff[src] * 2, 6 * 3600)
                self.fails[src] = 0
        else:
            self.fails[src] = 0
            self.backoff[src] = 1800
            if price:
                self.store.set(part, mode, price, src)
                self.store.save()
                log.info("%s: %s = %s ₽", src, part.id, price)
        await asyncio.sleep(random.uniform(*self.delay[src]))

    async def _make_getter(self):
        if os.getenv("USE_BROWSER") == "1":
            try:
                g = BrowserGetter(self.proxy_url)
                await g.start()
                log.info("цены: используется браузер (Playwright)")
                return g
            except Exception as e:
                log.error("Playwright не запустился (%s). Выполните: pip install playwright && "
                          "playwright install chromium, либо задайте BROWSER_CHANNEL. Пока — обычный HTTP.", e)
        g = HttpGetter(self.proxy_url)
        await g.start()
        return g

    async def run(self):
        getter = await self._make_getter()
        dns_on = os.getenv("DNS_ENABLED") == "1"
        try:
            while True:
                for part in PARTS:
                    if part.new is not None and dns_on:
                        await self._one(getter, "DNS", part)
                    if part.used is not None:
                        await self._one(getter, "Авито", part)
                self.store.save()
                await asyncio.sleep(1800)
        finally:
            await getter.close()


@dataclass
class Item:
    part: Part
    price: int
    src: str


@dataclass
class Build:
    items: list[Item]
    total: int
    score: float

    def get(self, kind: str) -> Part | None:
        return next((i.part for i in self.items if i.part.kind == kind), None)


def _hm(a: float, b: float, wa: float) -> float:
    return 1 / (wa / max(a, 1) + (1 - wa) / max(b, 1))


def score(purpose: str, cpu: Part, gpu: Part, ram: Part, ssd: Part, case: Part) -> float:
    c, g = cpu.attrs, gpu.attrs
    gb, ssd_gb, vram = ram.attrs["gb"], ssd.attrs["gb"], g["vram"]
    cuda = g["vendor"] == "nvidia"

    if purpose == "game":
        s = _hm(g["perf"], c["game"], 0.7)
        s += {8: 0, 10: 1, 12: 2.5, 16: 3.5, 24: 4}.get(vram, 0) - (4 if vram < 8 else 0)
        s += {32: 1.5, 64: 1.5}.get(gb, 0) - (3 if gb < 16 else 0)
        s += {500: 0, 1000: 0.8, 2000: 1.2}[ssd_gb]
    elif purpose == "work":
        s = 0.4 * c["prod"] + 0.15 * g["perf"] + 3 * cuda
        s += {32: 10, 64: 16}.get(gb, 0)
        s += {500: 0, 1000: 4, 2000: 6}[ssd_gb]
        s += 0.02 * c["game"]
    elif purpose == "balance":
        gaming = _hm(g["perf"], c["game"], 0.65)
        prod = 0.4 * c["prod"] + 0.15 * g["perf"]
        s = 0.55 * gaming + 0.45 * prod + 2 * cuda
        s += {32: 5, 64: 7}.get(gb, 0)
        s += {500: 0, 1000: 2, 2000: 3}[ssd_gb]
        s += {8: 0, 10: 0.5, 12: 1.5, 16: 2.5, 24: 3}.get(vram, 0)
    else:
        s = 0.5 * min(vram, 24) / 24 * 100 + 0.2 * g["perf"] + 6 * cuda
        s += {32: 7, 64: 12}.get(gb, 0)
        s += {500: 0, 1000: 5, 2000: 8}[ssd_gb]
        s += 0.05 * c["prod"] / 1.5
    return s + case.attrs["bonus"] * 0.3


def build_options(purpose: str, condition: str, budget: int, store: PriceStore, top: int = 3) -> list[Build]:
    def avail(kind):
        out = []
        for p in BY_KIND.get(kind, []):
            pr = store.get(p, condition)
            if pr:
                out.append((p, pr[0], pr[1]))
        return sorted(out, key=lambda x: x[1])

    cpus, gpus = avail("cpu"), avail("gpu")
    ssds, cases, coolers, psus, boards = (avail(k) for k in ("ssd", "case", "cooler", "psu", "mb"))
    all_ram = avail("ram")
    rams = {t: [r for r in all_ram if r[0].attrs["type"] == t] for t in ("DDR4", "DDR5")}
    if not (ssds and cases and psus and gpus):
        return []
    cheap_tail = ssds[0][1] + cases[0][1]

    cands = []
    for cpu, cpu_p, cpu_s in cpus:
        ca = cpu.attrs
        cooler = None
        if ca["cooler"]:
            cooler = next((c for c in coolers if c[0].attrs["tier"] >= ca["cooler"]), None)
            if cooler is None:
                continue
        for rtype, ram_list in rams.items():
            board = next((b for b in boards if b[0].attrs["socket"] == ca["socket"]
                          and b[0].attrs["ram"] == rtype and b[0].attrs["tier"] >= ca["tier"]), None)
            if board is None or not ram_list:
                continue
            base = cpu_p + board[1] + (cooler[1] if cooler else 0) + ram_list[0][1] + cheap_tail
            if base > budget:
                continue
            for gpu, gpu_p, gpu_s in gpus:
                need = gpu.attrs["tdp"] + 1.5 * ca["tdp"] + 150
                psu = next((p for p in psus if p[0].attrs["watts"] >= need), None)
                if psu is None:
                    continue
                base2 = base + gpu_p + psu[1]
                if base2 > budget:
                    break
                fixed = cpu_p + board[1] + (cooler[1] if cooler else 0) + gpu_p + psu[1]
                for ram in ram_list:
                    for ssd in ssds:
                        for case in cases:
                            total = fixed + ram[1] + ssd[1] + case[1]
                            if total > budget:
                                continue
                            s = score(purpose, cpu, gpu, ram[0], ssd[0], case[0])
                            items = [(cpu, cpu_p, cpu_s), board, ram, (gpu, gpu_p, gpu_s), ssd, psu, case]
                            if cooler:
                                items.insert(1, cooler)
                            cands.append((s, -total, items))

    cands.sort(key=lambda x: (x[0], x[1]), reverse=True)
    builds, seen = [], set()
    for s, neg_total, items in cands:
        key = (items[0][0].id, next(i[0].id for i in items if i[0].kind == "gpu"))
        if key in seen:
            continue
        seen.add(key)
        builds.append(Build([Item(*i) for i in items], -neg_total, round(s, 1)))
        if len(builds) == top:
            break
    return builds


store = PriceStore()
dp = Dispatcher()


class Flow(StatesGroup):
    budget = State()


def kb(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t, callback_data=d) for t, d in row] for row in rows
    ])


def purpose_kb():
    items = [(name, f"p:{key}") for key, name in PURPOSES.items()]
    return kb([items[:2], items[2:]])


def condition_kb():
    return kb([
        [("🆕 Новое (DNS)", "c:new"), ("♻️ Б/у (Авито)", "c:used")],
        [("⬅️ Назад", "nav:purpose")],
    ])


def budget_kb():
    presets = [50_000, 70_000, 100_000, 150_000, 250_000]
    btns = [(f"{p // 1000}к", f"b:{p}") for p in presets]
    return kb([btns[:3], btns[3:], [("⬅️ Назад", "nav:condition")]])


def result_kb(n: int, idx: int):
    variants = [(("• " if i == idx else "") + f"Вариант {i + 1}", f"v:{i}") for i in range(n)]
    return kb([variants, [("💰 Другой бюджет", "nav:budget"), ("🎯 Другая цель", "nav:purpose")]])


def rub(n: int) -> str:
    return f"{n:,}".replace(",", " ") + " ₽"


def parse_budget(text: str) -> int | None:
    t = re.sub(r"\s|₽|руб|р", "", text.lower())
    m = re.fullmatch(r"(\d+(?:[.,]\d+)?)(к|k|тыс)?", t)
    if not m:
        return None
    value = float(m.group(1).replace(",", "."))
    return int(value * 1000 if m.group(2) else value)


LLM_HINTS = {
    6: "до ~3–4B моделей (Q4)",
    8: "модели до ~7–8B (Q4)",
    10: "модели до ~8–9B (Q4/Q6)",
    12: "модели до ~13–14B (Q4)",
    16: "модели до ~20–24B (Q4)",
    24: "модели до ~32–34B (Q4), 70B — с выгрузкой в ОЗУ",
}


def render(b: Build, purpose: str, condition: str, budget: int, idx: int, total_variants: int) -> str:
    mode = "🆕 новое, цены DNS" if condition == "new" else "♻️ б/у с Авито (SSD и БП — новые, DNS)"
    lines = [f"<b>{PURPOSES[purpose]}</b> · {mode}",
             f"Бюджет: <b>{rub(budget)}</b> · вариант {idx + 1}/{total_variants}", ""]
    estimated = False
    for kind in ORDER:
        for it in (i for i in b.items if i.part.kind == kind):
            est = it.src == "оценка"
            estimated |= est
            lines.append(f"<b>{KIND_RU[kind]}:</b> {html.escape(it.part.name)}\n"
                         f"    {'~' if est else ''}{rub(it.price)} <i>({it.src})</i>")
    lines += ["", f"<b>Итого: {rub(b.total)}</b> · остаток {rub(budget - b.total)}"]

    gpu = b.get("gpu")
    if gpu:
        vram = gpu.attrs["vram"]
        if purpose == "llm":
            lines.append(f"\n🧠 VRAM {vram} ГБ — {LLM_HINTS.get(vram, '')}.")
        elif vram <= 8 and budget >= 100_000:
            lines.append("\nℹ️ У видеокарты всего 8 ГБ памяти — для новых игр в высоком качестве маловато.")
    if estimated:
        lines.append("\n<i>~ — оценочная цена: живая цена с DNS/Авито пока не получена.</i>")
    if condition == "used":
        lines.append("\n⚠️ <i>Б/у: проверяйте видеокарту (майнинг, ремонт), просите тесты "
                     "и берите через «Авито Доставку» с проверкой.</i>")
    return "\n".join(lines)


async def show_result(target: Message, state: FSMContext, idx: int = 0, edit: bool = False):
    d = await state.get_data()
    builds = await asyncio.to_thread(build_options, d["purpose"], d["condition"], d["budget"], store)
    if not builds:
        text = ("😕 На эту сумму не получилось собрать совместимую сборку.\n"
                "Попробуйте увеличить бюджет или выбрать другой режим.")
        markup = kb([[("💰 Другой бюджет", "nav:budget"), ("🎯 Другая цель", "nav:purpose")]])
    else:
        idx = min(idx, len(builds) - 1)
        text = render(builds[idx], d["purpose"], d["condition"], d["budget"], idx, len(builds))
        markup = result_kb(len(builds), idx)
    await (target.edit_text if edit else target.answer)(text, reply_markup=markup)


START_TEXT = ("👋 Я подберу комплектующие для ПК под ваш бюджет и задачи.\n\n"
              f"Бюджет — от {rub(MIN_BUDGET)}. Для нового беру цены DNS, для б/у — Авито.\n\n"
              "<b>Для чего собираем ПК?</b>")


def condition_text(purpose: str) -> str:
    return f"{PURPOSES[purpose]}\n\n<b>Какие комплектующие берём?</b>"


@dp.message(CommandStart())
@dp.message(Command("new"))
async def command_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(START_TEXT, reply_markup=purpose_kb())


@dp.callback_query(F.data == "nav:purpose")
async def nav_purpose(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.edit_text(START_TEXT, reply_markup=purpose_kb())
    await cb.answer()


@dp.callback_query(F.data.startswith("p:"))
async def choose_purpose(cb: CallbackQuery, state: FSMContext):
    purpose = cb.data.split(":")[1]
    if purpose not in PURPOSES:
        return await cb.answer()
    await state.update_data(purpose=purpose)
    await cb.message.edit_text(condition_text(purpose), reply_markup=condition_kb())
    await cb.answer()


@dp.callback_query(F.data == "nav:condition")
async def nav_condition(cb: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    if "purpose" not in d:
        return await nav_purpose(cb, state)
    await state.set_state(None)
    await cb.message.edit_text(condition_text(d["purpose"]), reply_markup=condition_kb())
    await cb.answer()


async def ask_budget(message: Message, state: FSMContext):
    await state.set_state(Flow.budget)
    await message.edit_text(
        f"<b>Какой бюджет?</b>\nВыберите кнопкой или напишите сумму "
        f"(от {rub(MIN_BUDGET)}), например <code>85000</code> или <code>85к</code>.",
        reply_markup=budget_kb())


@dp.callback_query(F.data.startswith("c:"))
async def choose_condition(cb: CallbackQuery, state: FSMContext):
    cond = cb.data.split(":")[1]
    if cond not in ("new", "used"):
        return await cb.answer()
    await state.update_data(condition=cond)
    await ask_budget(cb.message, state)
    await cb.answer()


@dp.callback_query(F.data == "nav:budget")
async def nav_budget(cb: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    if "condition" not in d:
        return await nav_purpose(cb, state)
    await ask_budget(cb.message, state)
    await cb.answer()


@dp.callback_query(F.data.startswith("b:"))
async def choose_budget_button(cb: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    if "purpose" not in d or "condition" not in d:
        return await nav_purpose(cb, state)
    await state.update_data(budget=int(cb.data.split(":")[1]))
    await state.set_state(None)
    await cb.message.edit_text("⏳ Подбираю…")
    await show_result(cb.message, state, edit=True)
    await cb.answer()


@dp.message(Flow.budget, F.text)
async def choose_budget_text(message: Message, state: FSMContext):
    budget = parse_budget(message.text)
    if budget is None:
        return await message.answer("Не понял сумму 🤔 Напишите числом, например <code>85000</code> или <code>85к</code>.")
    if budget < MIN_BUDGET:
        return await message.answer(f"Минимальный бюджет — {rub(MIN_BUDGET)}. Введите сумму побольше.")
    if budget > MAX_BUDGET:
        return await message.answer(f"Давайте не больше {rub(MAX_BUDGET)} 🙂")
    await state.update_data(budget=budget)
    await state.set_state(None)
    await show_result(message, state)


@dp.callback_query(F.data.startswith("v:"))
async def switch_variant(cb: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    if not {"purpose", "condition", "budget"} <= d.keys():
        return await cb.answer("Сессия устарела, начните заново: /start", show_alert=True)
    await show_result(cb.message, state, idx=int(cb.data.split(":")[1]), edit=True)
    await cb.answer()


@dp.message()
async def fallback(message: Message):
    await message.answer("Нажмите /start, чтобы подобрать сборку.")


async def main():
    token = "8953531728:AAHTU7tkja-ioCsfhpISD_g4zOJaHSiJVNQ"
    if not token:
        raise SystemExit("Не задан токен бота")
    bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    updater_task = None
    if os.getenv("LIVE_PRICES") == "1":
        updater_task = asyncio.create_task(PriceUpdater(store).run())
    try:
        await dp.start_polling(bot)
    finally:
        if updater_task:
            updater_task.cancel()
        store.save()


if __name__ == "__main__":
    asyncio.run(main())
