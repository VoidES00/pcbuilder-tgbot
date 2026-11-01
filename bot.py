import asyncio
import html
import json
import logging
import os
import random
import re
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
    "CITILINK_ENABLED": "1",
}.items():
    os.environ.setdefault(_k, _v)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("pcbuilder")

BASE_DIR = Path(__file__).resolve().parent
CACHE_FILE = BASE_DIR / "prices_cache.json"
TTL = 48 * 3600
MIN_BUDGET = 50_000
MAX_BUDGET = 1_000_000
SERVER_MIN_BUDGET = 100_000       # серверные сборки — от 100 тыс. ₽
SERVER_MAX_BUDGET = 1_500_000
SERVER_BUDGET_PRESETS = [100_000, 150_000, 250_000, 400_000, 700_000]
NEW_ONLY = {"ssd", "psu"}
# В парах CPU↔GPU не рекомендуем деталь, которая стоит больше базовой более чем в MAX_PRICE_RATIO раз,
# чтобы не подбирать топовый процессор к бюджетной видеокарте (и наоборот).
MAX_PRICE_RATIO = 3.0
UA_OS = "Windows NT 10.0; Win64; x64" if sys.platform == "win32" else "X11; Linux x86_64"
HEADERS = {
    "User-Agent": f"Mozilla/5.0 ({UA_OS}) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Accept": "text/html,application/xhtml+xml",
}
CITI_WAIT = '[data-meta-price], [class*="price"]'
NEW_SOURCES = ("Ситилинк",)
USED_SOURCES = ("Авито",)
SOURCE_FLAGS = {"Ситилинк": "CITILINK_ENABLED"}
PSU_MULT = 1.3
PSU_BASE = 150
PSU_FIXED = 200
SAMPLE = 5  
AVITO_WAIT = '[data-marker="item-price"], [itemprop="price"]'

PURPOSES = {
    "game": "🎮 Игры",
    "work": "💼 Работа",
    "balance": "⚖️ Баланс",
    "llm": "🧠 LLM / нейросети",
}
SERVER_PURPOSES = {
    "server_base": "🗄 Базовые задачи",
    "server_llm": "🧠 LLM-сервер",
    "server_heavy": "🔥 Тяжёлые задачи",
}
ALL_PURPOSES = {**PURPOSES, **SERVER_PURPOSES}
KIND_RU = {
    "cpu": "Процессор", "cooler": "Охлаждение", "mb": "Материнская плата", "ram": "Память",
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
    ("r5_5600", "AMD Ryzen 5 5600", "AM4", 58, 46, 65, 0, 2, 9500, 7000),
    ("r7_5700x", "AMD Ryzen 7 5700X", "AM4", 60, 62, 65, 1, 2, 15000, 11500),
    ("r7_5700x3d", "AMD Ryzen 7 5700X3D", "AM4", 72, 62, 105, 1, 2, 24000, 19000),
    ("r9_5900x", "AMD Ryzen 9 5900X", "AM4", 62, 85, 105, 2, 3, 24000, 16000),
    ("r5_7500f", "AMD Ryzen 5 7500F", "AM5", 70, 55, 65, 1, 2, 12500, 10500),
    ("r5_9600x", "AMD Ryzen 5 9600X", "AM5", 78, 62, 65, 1, 2, 21000, 18000),
    ("r7_7700", "AMD Ryzen 7 7700", "AM5", 76, 72, 65, 0, 2, 25000, 20000),
    ("r7_9700x", "AMD Ryzen 7 9700X", "AM5", 82, 75, 65, 1, 2, 31000, 27000),
    ("r7_7800x3d", "AMD Ryzen 7 7800X3D", "AM5", 100, 74, 120, 2, 3, 38000, 32000),
    ("r9_7900", "AMD Ryzen 9 7900", "AM5", 78, 100, 65, 0, 2, 36000, 28000),
    ("r9_7950x", "AMD Ryzen 9 7950X", "AM5", 82, 135, 170, 3, 3, 42000, 34000),
    ("r9_9950x", "AMD Ryzen 9 9950X", "AM5", 90, 150, 170, 3, 3, 58000, 48000),
    ("r9_5950x", "AMD Ryzen 9 5950X", "AM4", 70, 108, 105, 2, 2, None, 30000),
    ("i3_12100f", "Intel Core i3-12100F", "LGA1700", 50, 32, 58, 0, 1, 5500, 4500),
    ("i5_12400f", "Intel Core i5-12400F", "LGA1700", 58, 46, 65, 0, 2, 9500, 7500),
    ("i5_13400f", "Intel Core i5-13400F", "LGA1700", 64, 60, 65, 0, 2, 14000, 11000),
    ("i5_14400f", "Intel Core i5-14400F", "LGA1700", 66, 62, 65, 0, 2, 15500, 13000),
    ("i5_14600kf", "Intel Core i5-14600KF", "LGA1700", 80, 90, 125, 2, 3, 23000, 19000),
    ("i7_14700f", "Intel Core i7-14700F", "LGA1700", 84, 115, 65, 2, 3, 33000, 27000),
    ("i9_14900k", "Intel Core i9-14900K", "LGA1700", 95, 145, 253, 4, 3, 48000, 40000),
    # --- Серверные процессоры (Xeon, EPYC, Threadripper PRO) ---
    ("xeon_w3300", "Intel Xeon W-3300 (Ice Lake-W)", "LGA4189", 55, 70, 220, 4, 4, 85000, 60000),
    ("xeon_w3400", "Intel Xeon W-3400 (Sapphire Rapids)", "LGA4677", 75, 110, 270, 4, 4, 120000, 85000),
    ("xeon_w3500", "Intel Xeon W-3500 (Emerald Rapids)", "LGA4677", 80, 120, 300, 4, 4, 150000, 110000),
    ("epyc_7313", "AMD EPYC 7313 (Milan, 16c)", "SP3", 60, 85, 155, 4, 4, 90000, 65000),
    ("epyc_7543", "AMD EPYC 7543 (Milan, 32c)", "SP3", 70, 130, 225, 4, 4, 160000, 110000),
    ("epyc_7763", "AMD EPYC 7763 (Milan, 64c)", "SP3", 75, 160, 280, 4, 4, 280000, 200000),
    ("epyc_9354", "AMD EPYC 9354 (Genoa, 32c)", "SP5", 85, 150, 280, 4, 4, 220000, 160000),
    ("epyc_9654", "AMD EPYC 9654 (Genoa, 96c)", "SP5", 90, 200, 360, 4, 4, 450000, 350000),
    ("tr_pro_5965wx", "AMD Ryzen Threadripper PRO 5965WX (24c)", "sWRX8", 85, 140, 280, 4, 4, 250000, 180000),
    ("tr_pro_5975wx", "AMD Ryzen Threadripper PRO 5975WX (32c)", "sWRX8", 88, 155, 280, 4, 4, 320000, 240000),
    ("tr_pro_5995wx", "AMD Ryzen Threadripper PRO 5995WX (64c)", "sWRX8", 92, 180, 280, 4, 4, 550000, 420000),
    ("tr_pro_7965wx", "AMD Ryzen Threadripper PRO 7965WX (24c, Zen 4)", "sTR5", 95, 160, 350, 4, 4, 300000, 230000),
    ("tr_pro_7975wx", "AMD Ryzen Threadripper PRO 7975WX (32c, Zen 4)", "sTR5", 98, 175, 350, 4, 4, 400000, 300000),
    ("tr_pro_7995wx", "AMD Ryzen Threadripper PRO 7995WX (96c, Zen 4)", "sTR5", 100, 220, 350, 4, 4, 750000, 580000),
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
    ("rtx5090", "GeForce RTX 5090 32GB", "nvidia", 118, 32, 575, 320000, 265000),
    ("rtx_a6000", "NVIDIA RTX A6000 48GB", "nvidia", 90, 48, 300, 480000, 300000),
    ("rtx2080ti22", "GeForce RTX 2080 Ti 22GB (мод)", "nvidia", 40, 22, 300, None, 45000),
]:
    i, name, vendor, perf, vram, tdp, new, used = row
    add(i, "gpu", name, new, used, vendor=vendor, perf=perf, vram=vram, tdp=tdp)

for i, name, socket, ram, tier, new, used in [
    ("am4_a520", "Gigabyte A520M K V2", "AM4", "DDR4", 1, 5500, 3500),
    ("am4_a520b", "ASRock A520M-HDV", "AM4", "DDR4", 1, 5200, 3200),
    ("am4_b550", "MSI B550M PRO-VDH WIFI", "AM4", "DDR4", 2, 9500, 6500),
    ("am4_b550b", "Gigabyte B550M DS3H", "AM4", "DDR4", 2, 9000, 6000),
    ("am4_b550c", "ASUS TUF GAMING B550-PLUS", "AM4", "DDR4", 2, 14500, 10000),
    ("am4_b550d", "MSI MAG B550 TOMAHAWK", "AM4", "DDR4", 2, 15500, 11000),
    ("am4_x570", "Gigabyte X570 AORUS ELITE", "AM4", "DDR4", 3, 17500, 12500),
    ("am4_x570b", "ASUS TUF GAMING X570-PLUS", "AM4", "DDR4", 3, 19500, 14000),
    ("am5_a620", "ASRock A620M-HDV/M.2", "AM5", "DDR5", 1, 8500, 7000),
    ("am5_a620b", "Gigabyte A620M S2H", "AM5", "DDR5", 1, 9000, 7200),
    ("am5_b650", "MSI PRO B650M-P", "AM5", "DDR5", 2, 13500, 10500),
    ("am5_b650b", "Gigabyte B650M DS3H", "AM5", "DDR5", 2, 14500, 11500),
    ("am5_b650c", "ASUS TUF GAMING B650-PLUS WIFI", "AM5", "DDR5", 2, 22000, 17500),
    ("am5_b650d", "MSI MAG B650 TOMAHAWK WIFI", "AM5", "DDR5", 2, 23500, 18500),
    ("am5_b650e", "ASUS ROG STRIX B650E-F GAMING WIFI", "AM5", "DDR5", 3, 30000, 24000),
    ("am5_x670e", "MSI MAG X670E TOMAHAWK WIFI", "AM5", "DDR5", 3, 33000, 26000),
    ("am5_x670eb", "Gigabyte X670E AORUS MASTER", "AM5", "DDR5", 3, 52000, 40000),
    ("1700_h610", "ASRock H610M-HDV/M.2 (DDR4)", "LGA1700", "DDR4", 1, 5800, 4000),
    ("1700_h610b", "MSI PRO H610M-E (DDR4)", "LGA1700", "DDR4", 1, 6500, 4500),
    ("1700_h610d5", "ASRock H610M-HDV/M.2 R2.0 (DDR5)", "LGA1700", "DDR5", 1, 7500, 5500),
    ("1700_b660", "MSI PRO B660M-A (DDR4)", "LGA1700", "DDR4", 2, 10500, 7500),
    ("1700_b660b", "Gigabyte B660M DS3H (DDR4)", "LGA1700", "DDR4", 2, 10000, 7000),
    ("1700_b760d4", "Gigabyte B760M DS3H (DDR4)", "LGA1700", "DDR4", 2, 11500, 8500),
    ("1700_b760", "MSI PRO B760M-A WIFI (DDR5)", "LGA1700", "DDR5", 2, 13500, 10500),
    ("1700_b760b", "Gigabyte B760M GAMING X AX (DDR5)", "LGA1700", "DDR5", 2, 15500, 12000),
    ("1700_b760c", "ASUS TUF GAMING B760-PLUS WIFI (DDR5)", "LGA1700", "DDR5", 2, 21000, 16500),
    ("1700_z690", "ASUS PRIME Z690-P D4 (DDR4)", "LGA1700", "DDR4", 3, 17000, 12500),
    ("1700_z790", "MSI PRO Z790-P WIFI (DDR5)", "LGA1700", "DDR5", 3, 21500, 16500),
    ("1700_z790b", "Gigabyte Z790 AORUS ELITE AX (DDR5)", "LGA1700", "DDR5", 3, 28000, 22000),
    ("1700_z790c", "ASUS ROG STRIX Z790-E GAMING WIFI (DDR5)", "LGA1700", "DDR5", 3, 45000, 34000),
    # --- Серверные материнские платы ---
    ("lga4189_w480", "ASUS Pro WS W480-ACE (Xeon W-3300)", "LGA4189", "DDR4", 3, 45000, 32000),
    ("lga4677_w790", "ASUS Pro WS W790E-SAGE SE (Xeon W-3400/3500)", "LGA4677", "DDR5", 4, 85000, 65000),
    ("lga4677_w790b", "Supermicro H13SSL-NT (Xeon W-3400/3500)", "LGA4677", "DDR5", 4, 75000, 55000),
    ("sp3_asus", "ASUS KRPA-U16 (EPYC Milan/Genoa)", "SP3", "DDR4", 4, 65000, 48000),
    ("sp3_supermicro", "Supermicro H12SSL-i (EPYC Milan)", "SP3", "DDR4", 4, 55000, 40000),
    ("sp5_asus", "ASUS KGPE-D16 (EPYC Genoa)", "SP5", "DDR5", 4, 85000, 65000),
    ("sp5_supermicro", "Supermicro H13SSL-NT (EPYC Genoa)", "SP5", "DDR5", 4, 78000, 58000),
    ("swrx8_asus", "ASUS Pro WS WRX80E-SAGE SE WIFI (TR PRO 5000)", "sWRX8", "DDR4", 4, 95000, 70000),
    ("swrx8_gigabyte", "Gigabyte MW32-SU0 (TR PRO 5000)", "sWRX8", "DDR4", 4, 85000, 62000),
    ("str5_asus", "ASUS Pro WS TRX50-SAGE WIFI (TR PRO 7000)", "sTR5", "DDR5", 4, 110000, 85000),
    ("str5_gigabyte", "Gigabyte TRX50 AERO D (TR PRO 7000)", "sTR5", "DDR5", 4, 100000, 78000),
]:
    add(i, "mb", name, new, used, socket=socket, ram=ram, tier=tier)

# Сколько видеокарт физически влезает в плату (полноразмерные слоты x16).
# Нужно только серверному подбору: две видеокарты ставим лишь в платы с 2+ слотами.
BOARD_GPU_SLOTS = {
    "am4_b550c": 2, "am4_b550d": 2, "am4_x570": 2, "am4_x570b": 2,
    "am5_b650c": 2, "am5_b650d": 2, "am5_b650e": 2, "am5_x670e": 2, "am5_x670eb": 3,
    "1700_b760c": 2, "1700_z690": 2, "1700_z790": 2, "1700_z790b": 2, "1700_z790c": 2,
    # Серверные платы — обычно 3-7 слотов x16
    "lga4189_w480": 4, "lga4677_w790": 7, "lga4677_w790b": 5,
    "sp3_asus": 3, "sp3_supermicro": 3,
    "sp5_asus": 5, "sp5_supermicro": 4,
    "swrx8_asus": 4, "swrx8_gigabyte": 4,
    "str5_asus": 4, "str5_gigabyte": 4,
}
for _b in PARTS:
    if _b.kind == "mb":
        _b.attrs["gpu_slots"] = BOARD_GPU_SLOTS.get(_b.id, 1)

for i, name, typ, gb, new, used in [
    ("d4_16", "Kingston Fury Beast 16GB (2x8) DDR4-3200", "DDR4", 16, 15000, 7000),
    ("d4_32", "Kingston Fury Beast 32GB (2x16) DDR4-3200", "DDR4", 32, 25000, 10000),
    ("d4_64", "Kingston Fury Beast 64GB (2x32) DDR4-3200", "DDR4", 64, 45000, 35000),
    ("d5_16", "Kingston Fury Beast 16GB (2x8) DDR5-5600", "DDR5", 16, 25000, 15000),
    ("d5_32", "Kingston Fury Beast 32GB (2x16) DDR5-6000", "DDR5", 32, 50000, 25000),
    ("d5_64", "Kingston Fury Beast 64GB (2x32) DDR5-5600", "DDR5", 64, 65000, 50000),
    ("d5_96", "Kingston Fury Beast 96GB (2x48) DDR5-5600", "DDR5", 96, 95000, 68000),
    ("d5_128", "Kingston Fury Beast 128GB (4x32) DDR5-5600", "DDR5", 128, 110000, 80000),
    ("d4_128", "Kingston Fury Beast 128GB (4x32) DDR4-3200", "DDR4", 128, 85000, 60000),
    # --- Серверная ECC память ---
    ("d4_32_ecc", "Samsung 32GB DDR4-3200 ECC RDIMM", "DDR4", 32, 18000, 12000),
    ("d4_64_ecc", "Samsung 64GB DDR4-3200 ECC RDIMM", "DDR4", 64, 35000, 24000),
    ("d4_128_ecc", "Samsung 128GB DDR4-3200 ECC RDIMM (2x64)", "DDR4", 128, 65000, 45000),
    ("d4_256_ecc", "Samsung 256GB DDR4-3200 ECC RDIMM (4x64)", "DDR4", 256, 120000, 85000),
    ("d5_32_ecc", "Samsung 32GB DDR5-4800 ECC RDIMM", "DDR5", 32, 35000, 25000),
    ("d5_64_ecc", "Samsung 64GB DDR5-4800 ECC RDIMM", "DDR5", 64, 65000, 48000),
    ("d5_128_ecc", "Samsung 128GB DDR5-4800 ECC RDIMM (2x64)", "DDR5", 128, 120000, 90000),
    ("d5_256_ecc", "Samsung 256GB DDR5-4800 ECC RDIMM (4x64)", "DDR5", 256, 220000, 160000),
    ("d5_512_ecc", "Samsung 512GB DDR5-4800 ECC RDIMM (8x64)", "DDR5", 512, 420000, 320000),
]:
    add(i, "ram", name, new, used, type=typ, gb=gb)

for i, name, gb, new in [
    ("ssd_500", "Kingston NV3 500GB NVMe", 500, 8000),
    ("ssd_1000", "Kingston NV3 1TB NVMe", 1000, 15000),
    ("ssd_2000", "Kingston NV3 2TB NVMe", 2000, 25000),
    ("ssd_4000", "Kingston NV3 4TB NVMe", 4000, 48000),
]:
    add(i, "ssd", name, new, None, gb=gb)

for i, name, watts, new in [
    ("psu_450", "DeepCool PF450 450W", 450, 3200),
    ("psu_500", "DeepCool PF500 500W", 500, 3500),
    ("psu_550", "Cougar GEX 550W", 550, 3900),
    ("psu_600", "DeepCool PF600 600W", 600, 4300),
    ("psu_650", "DeepCool PL650D 650W", 650, 6000),
    ("psu_650b", "Cougar GEX 650W", 650, 5200),
    ("psu_650c", "Corsair CV650 650W", 650, 5800),
    ("psu_700", "be quiet! System Power 10 700W", 700, 6800),
    ("psu_750", "MSI MAG A750GL 750W", 750, 8500),
    ("psu_750b", "DeepCool PN750M 750W", 750, 7800),
    ("psu_750c", "be quiet! Pure Power 12 M 750W", 750, 11500),
    ("psu_850", "MSI MAG A850GL 850W", 850, 11000),
    ("psu_850b", "Corsair RM850e 850W", 850, 14500),
    ("psu_850c", "Seasonic Focus GX-850 850W", 850, 16500),
    ("psu_1000", "MSI MAG A1000GL 1000W", 1000, 16000),
    ("psu_1000b", "Corsair RM1000e 1000W", 1000, 18500),
    ("psu_1000c", "be quiet! Straight Power 12 1000W", 1000, 23000),
    ("psu_1250", "MSI MPG A1250G 1250W", 1250, 22000),
    ("psu_1300", "Seasonic Focus GX-1300 1300W", 1300, 30000),
    ("psu_1600", "Corsair AX1600i 1600W", 1600, 42000),
    ("psu_1600b", "Seasonic Prime TX-1600 1600W", 1600, 45000),
    ("psu_2000", "FSP Cannon Pro 2000W", 2000, 55000),
    # --- Серверные БП высокой мощности ---
    ("psu_2200", "Super Flower Leadex 2200W", 2200, 68000),
    ("psu_2400", "FSP Y-2400 2400W (CRPS)", 2400, 75000),
    ("psu_2600", "Great Wall 2600W 80+ Titanium", 2600, 85000),
    ("psu_3000", "Delta 3000W CRPS Redundant", 3000, 120000),
]:
    add(i, "psu", name, new, None, watts=watts)

for i, name, bonus, new, used in [
    ("case_1", "Zalman S2", 0, 2500, 1500),
    ("case_1b", "Zalman T7", 0, 3000, 1800),
    ("case_1c", "Aerocool Cylon Mini", 0, 2800, 1600),
    ("case_2", "DeepCool CC560", 2, 5000, 3000),
    ("case_2b", "Zalman Z1 Neo", 1, 4200, 2500),
    ("case_2c", "Cougar MX330", 1, 4800, 2800),
    ("case_2d", "DeepCool CH360 (mATX)", 2, 7500, 5000),
    ("case_2e", "Montech AIR 903 MAX", 3, 7000, 4500),
    ("case_3", "Lian Li Lancool 216", 4, 9500, None),
    ("case_3b", "DeepCool CH560", 3, 9000, 6000),
    ("case_3c", "Fractal Design Pop Air", 3, 10500, 7000),
    ("case_3d", "Phanteks XT Pro", 3, 11000, 7500),
    ("case_3e", "NZXT H5 Flow", 4, 11500, 8000),
    ("case_4", "be quiet! Pure Base 500DX", 4, 12500, 8500),
    ("case_4b", "Corsair 4000D Airflow", 4, 13000, 9000),
    ("case_4c", "Fractal Design North", 5, 17500, 12500),
    ("case_4d", "Lian Li O11 Dynamic EVO", 5, 19000, 13500),
    ("case_4e", "Hyte Y60", 5, 21000, None),
    # --- Серверные корпуса (E-ATX, SSI-EEB, много GPU) ---
    ("case_srv_1", "Fractal Design Define 7 XL (E-ATX)", 4, 18000, 13000),
    ("case_srv_2", "Phanteks Enthoo 719 (E-ATX/SSI-EEB)", 5, 22000, 16000),
    ("case_srv_3", "Lian Li O11 Dynamic EVO XL (E-ATX)", 5, 25000, 18000),
    ("case_srv_4", "Corsair 7000D Airflow (E-ATX/SSI-EEB)", 5, 24000, 17000),
    ("case_srv_5", "Fractal Design Torrent (E-ATX, потрясающий продув)", 5, 23000, 17000),
    ("case_srv_6", "Supermicro CSE-743TQ-R1200B (4U rack, 1200W)", 3, 45000, 32000),
    ("case_srv_7", "Chenbro RM41300-FS81 (4U, 8x GPU, 2000W)", 2, 85000, 60000),
]:
    add(i, "case", name, new, used, bonus=bonus)

for i, name, tier, tdp, typ, new, used in [
    # Воздушные кулеры
    ("cool_1", "DeepCool AK400", 1, 150, "air", 2400, 1500),
    ("cool_2", "Thermalright Peerless Assassin 120 SE", 2, 220, "air", 3500, 2500),
    ("cool_3", "ID-Cooling SE-224-XT", 1, 180, "air", 2200, 1200),
    ("cool_4", "DeepCool AG400", 1, 150, "air", 2300, 1400),
    ("cool_5", "Thermalright Assassin King 120 SE", 1, 180, "air", 2700, 1600),
    ("cool_6", "DeepCool AK500", 2, 200, "air", 4200, 2800),
    ("cool_7", "Thermalright Phantom Spirit 120 SE", 3, 250, "air", 4200, 3000),
    ("cool_8", "DeepCool AK620", 3, 240, "air", 5200, 3500),
    ("cool_9", "Noctua NH-D15", 4, 250, "air", 11000, 7500),
    ("cool_10", "Noctua NH-U14S TR4-SP3", 4, 250, "air", 13000, 9000),
    ("cool_11", "Noctua NH-U12A", 4, 250, "air", 12000, 8500),
    ("cool_12", "Thermalright Frozen Magic 240 Black", 4, 280, "air", 5500, 3800),
    # СЖО (AIO)
    ("cool_aio240_1", "DeepCool LE520 240 (СЖО)", 2, 220, "aio", 5500, 3500),
    ("cool_aio240_2", "DeepCool LT520 240 (СЖО)", 3, 250, "aio", 8000, 5500),
    ("cool_aio240_3", "Arctic Liquid Freezer III 240 (СЖО)", 3, 260, "aio", 9000, 6000),
    ("cool_aio280_1", "Arctic Liquid Freezer III 280 (СЖО)", 3, 280, "aio", 11000, 7500),
    ("cool_aio360_1", "Arctic Liquid Freezer III 360 (СЖО)", 4, 320, "aio", 13000, 9000),
    ("cool_aio360_2", "DeepCool LT720 360 (СЖО)", 4, 320, "aio", 12000, 8000),
    ("cool_aio360_3", "Lian Li Galahad II Trinity 360 (СЖО)", 4, 320, "aio", 14500, 10000),
    ("cool_aio360_4", "NZXT Kraken 360 (СЖО)", 4, 300, "aio", 18000, 12000),
    # --- Серверные СЖО и топовые воздушные ---
    ("cool_aio360_5", "Arctic Liquid Freezer III 360 TR4-SP3 (СЖО)", 4, 350, "aio", 15000, 11000),
    ("cool_aio420_1", "Arctic Liquid Freezer III 420 (СЖО)", 4, 380, "aio", 16000, 12000),
    ("cool_aio420_2", "EKWB EK-Nucleus AIO CR360 Dark (СЖО)", 4, 360, "aio", 18000, 13000),
    ("cool_aio420_3", "Corsair iCUE H170i Elite LCD (СЖО 420)", 4, 380, "aio", 22000, 16000),
    ("cool_tr4_1", "Noctua NH-U14S TR4-SP3 (Threadripper/EPYC)", 4, 250, "air", 13000, 9000),
    ("cool_tr4_2", "Noctua NH-U12S TR4-SP3 (Threadripper/EPYC)", 4, 220, "air", 11000, 7500),
    ("cool_sTR5_1", "Noctua NH-U14S TR5-SP5 (sTR5/SP5)", 4, 280, "air", 15000, 11000),
    ("cool_sTR5_2", "Thermalright Phantom Spirit 120 SE TR5", 4, 300, "air", 6000, 4200),
]:
    add(i, "cooler", name, new, used, tier=tier, tdp=tdp, type=typ)

BY_KIND: dict[str, list[Part]] = {}
for _p in PARTS:
    BY_KIND.setdefault(_p.kind, []).append(_p)
BY_ID: dict[str, Part] = {p.id: p for p in PARTS}

# Серверные комплектующие (Xeon, EPYC, Threadripper PRO и платы/память/БП/корпуса/кулеры под них).
# В пользовательских режимах (игра, работа, баланс, подбор CPU↔GPU) они не участвуют.
SERVER_IDS = frozenset({
    # Процессоры
    "xeon_w3300", "xeon_w3400", "xeon_w3500",
    "epyc_7313", "epyc_7543", "epyc_7763", "epyc_9354", "epyc_9654",
    "tr_pro_5965wx", "tr_pro_5975wx", "tr_pro_5995wx",
    "tr_pro_7965wx", "tr_pro_7975wx", "tr_pro_7995wx",
    # Материнские платы
    "lga4189_w480", "lga4677_w790", "lga4677_w790b",
    "sp3_asus", "sp3_supermicro",
    "sp5_asus", "sp5_supermicro",
    "swrx8_asus", "swrx8_gigabyte",
    "str5_asus", "str5_gigabyte",
    # Память (ECC)
    "d4_32_ecc", "d4_64_ecc", "d4_128_ecc", "d4_256_ecc",
    "d5_32_ecc", "d5_64_ecc", "d5_128_ecc", "d5_256_ecc", "d5_512_ecc",
    # Блоки питания
    "psu_2200", "psu_2400", "psu_2600", "psu_3000",
    # Корпуса
    "case_srv_1", "case_srv_2", "case_srv_3", "case_srv_4",
    "case_srv_5", "case_srv_6", "case_srv_7",
    # Охлаждение
    "cool_aio360_5", "cool_aio420_1", "cool_aio420_2", "cool_aio420_3",
    "cool_tr4_1", "cool_tr4_2", "cool_sTR5_1", "cool_sTR5_2",
    # Видеокарты (профессиональные/серверные)
    "rtx_a6000",
})


def is_server(part: Part) -> bool:
    return part.id in SERVER_IDS


def consumer_parts(kind: str) -> list[Part]:
    """Только пользовательские (не серверные) детали данного вида."""
    return [p for p in BY_KIND.get(kind, []) if not is_server(p)]


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

    def set(self, part: Part, mode: str, lo: int, hi: int, src: str):
        self.data[f"{part.id}:{mode}:{src}"] = {"price": lo, "max": hi, "src": src, "ts": time.time()}

    def fresh(self, part: Part, mode: str, src: str) -> bool:
        e = self.data.get(f"{part.id}:{mode}:{src}")
        return bool(e) and time.time() - e["ts"] < TTL

    def get(self, part: Part, condition: str) -> tuple[int, str, int] | None:
        """(мин. цена, источник, макс. цена) по свежим данным; иначе — оценка (мин = макс)."""
        mode = mode_for(part, condition)
        if mode is None:
            return None
        now = time.time()
        sources = NEW_SOURCES if mode == "new" else USED_SOURCES
        live = [(e["price"], e["src"], e.get("max", e["price"])) for s in sources
                if (e := self.data.get(f"{part.id}:{mode}:{s}")) and now - e["ts"] < TTL]
        if live:
            return min(live)
        est = getattr(part, mode)
        return est, "оценка", est


def _ints(pattern: str, text: str) -> list[int]:
    return [int(d) for m in re.findall(pattern, text) if (d := re.sub(r"\D", "", m))]


def _sane(values: list[int], ref: int) -> list[int]:
    return [v for v in values if 0.4 * ref <= v <= 2.5 * ref]


async def fetch_citilink(get, part: Part) -> tuple[int, int] | None:
    """Мин.–макс. цена среди первых SAMPLE товаров в выдаче Ситилинка."""
    page = await get(f"https://www.citilink.ru/search/?text={quote_plus(part.query)}", CITI_WAIT)
    raw: list[int] = []
    for pat in (r'data-meta-price="(\d+)"',
                r'"price"\s*:\s*"?(\d{4,7})',
                r'price[^>"]*"[^>]*>\s*([\d\s\u00a0\u202f]+?)\s*₽'):
        raw = _ints(pat, page)[:15]
        if raw:
            break
    prices = _sane(raw, part.new)[:SAMPLE]
    if not prices:
        log.info("Ситилинк: %s — после фильтра 0 цен (ожидалось ~%d ₽; выдача: %s)", part.id, part.new, raw)
        return None
    return min(prices), max(prices)


async def fetch_avito(get, part: Part) -> tuple[int, int] | None:
    """Мин.–макс. цена среди первых SAMPLE подходящих объявлений Авито."""
    region = os.getenv("AVITO_REGION", "rossiya")
    page = await get(f"https://www.avito.ru/{region}/tovary_dlya_kompyutera?q={quote_plus(part.query)}", AVITO_WAIT)
    raw = (_ints(r'itemprop="price"[^>]*content="(\d+)"', page)
           or _ints(r'content="(\d+)"[^>]*itemprop="price"', page))[:30]
    prices = _sane(raw, part.used)
    if len(prices) < 3:
        log.info("Авито: %s — после фильтра %d цен (ожидалось ~%d ₽; выдача: %s)",
                 part.id, len(prices), part.used, sorted(raw)[:8])
        return None
    sample = prices[:SAMPLE]
    return min(sample), max(sample)


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
    SOURCES = {"Ситилинк": ("new", fetch_citilink), "Авито": ("used", fetch_avito)}

    def __init__(self, store: PriceStore):
        self.store = store
        self.proxy_url = os.getenv("PROXY_URL") or None
        self.pause_until = dict.fromkeys(self.SOURCES, 0.0)
        self.fails = dict.fromkeys(self.SOURCES, 0)
        self.backoff = dict.fromkeys(self.SOURCES, 1800)
        self.logged_pause: dict[str, float] = {}
        self.delay = {"Ситилинк": (4, 8),
                      "Авито": (int(os.getenv("AVITO_DELAY_MIN")), int(os.getenv("AVITO_DELAY_MAX")))}

    async def _one(self, get, src: str, part: Part):
        mode, fn = self.SOURCES[src]
        now = time.time()
        if now < self.pause_until[src]:
            if self.logged_pause.get(src) != self.pause_until[src]:
                log.info("%s: пауза ещё ~%d мин, детали пропускаются", src, int(self.pause_until[src] - now) // 60)
                self.logged_pause[src] = self.pause_until[src]
            return
        if self.store.fresh(part, mode, src):
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
                self.store.set(part, mode, price[0], price[1], src)
                self.store.save()
                log.info("%s: %s = %s–%s ₽", src, part.id, price[0], price[1])
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
        enabled = [s for s in self.SOURCES if s not in SOURCE_FLAGS or os.getenv(SOURCE_FLAGS[s]) == "1"]
        log.info("источники цен: %s", ", ".join(enabled))
        try:
            while True:
                for part in PARTS:
                    for src in enabled:
                        if getattr(part, self.SOURCES[src][0]) is not None:
                            await self._one(getter, src, part)
                self.store.save()
                await asyncio.sleep(1800)
        finally:
            await getter.close()


@dataclass
class Item:
    part: Part
    price: int
    src: str
    hi: int


@dataclass
class Build:
    items: list[Item]
    total: int
    score: float

    def get(self, kind: str) -> Part | None:
        return next((i.part for i in self.items if i.part.kind == kind), None)

    def get_all(self, kind: str) -> list[Part]:
        return [i.part for i in self.items if i.part.kind == kind]


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
        s += {500: 0, 1000: 0.8, 2000: 1.2, 4000: 1.5}.get(ssd_gb, 0)
    elif purpose == "work":
        s = 0.4 * c["prod"] + 0.15 * g["perf"] + 3 * cuda
        s += {32: 10, 64: 16}.get(gb, 0)
        s += {500: 0, 1000: 4, 2000: 6, 4000: 8}.get(ssd_gb, 0)
        s += 0.02 * c["game"]
    elif purpose == "balance":
        gaming = _hm(g["perf"], c["game"], 0.65)
        prod = 0.4 * c["prod"] + 0.15 * g["perf"]
        s = 0.55 * gaming + 0.45 * prod + 2 * cuda
        s += {32: 5, 64: 7}.get(gb, 0)
        s += {500: 0, 1000: 2, 2000: 3, 4000: 4}.get(ssd_gb, 0)
        s += {8: 0, 10: 0.5, 12: 1.5, 16: 2.5, 24: 3}.get(vram, 0)
    else:
        s = 0.6 * min(vram, 48) / 48 * 100 + 0.2 * g["perf"] + 6 * cuda
        s += {32: 7, 64: 12, 96: 16, 128: 20}.get(gb, 0)
        s += {500: 0, 1000: 5, 2000: 8, 4000: 11}.get(ssd_gb, 0)
        s += 0.05 * c["prod"] / 1.5
    return s + case.attrs["bonus"] * 0.3


def build_options(purpose: str, condition: str, budget: int, store: PriceStore, top: int = 3) -> list[Build]:
    def avail(kind):
        out = []
        for p in BY_KIND.get(kind, []):
            if is_server(p):
                continue
            pr = store.get(p, condition)
            if pr:
                out.append((p, pr[0], pr[1], pr[2]))
        return sorted(out, key=lambda x: x[1])

    cpus, gpus = avail("cpu"), avail("gpu")
    ssds, cases, coolers, psus, boards = (avail(k) for k in ("ssd", "case", "cooler", "psu", "mb"))
    all_ram = avail("ram")
    rams = {t: [r for r in all_ram if r[0].attrs["type"] == t] for t in ("DDR4", "DDR5")}
    if not (ssds and cases and psus and gpus):
        return []
    cheap_tail = ssds[0][1] + cases[0][1]

    cands = []
    for cpu, cpu_p, cpu_s, cpu_hi in cpus:
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
            for gpu, gpu_p, gpu_s, gpu_hi in gpus:
                psu_hi = next((p for p in psus if p[0].attrs["watts"] >= psu_need(gpu, cpu)), None)
                psu_lo = next((p for p in psus if p[0].attrs["watts"] >= psu_need(gpu, cpu, True)), None)
                if psu_lo is None:
                    continue
                psu_hi = psu_hi or psu_lo
                if base + gpu_p + psu_lo[1] > budget:
                    break
                fixed = cpu_p + board[1] + (cooler[1] if cooler else 0) + gpu_p
                for ram in ram_list:
                    for ssd in ssds:
                        for case in cases:
                            rest = fixed + ram[1] + ssd[1] + case[1]
                            psu = psu_hi
                            if rest + psu[1] > budget:
                                psu = psu_lo
                            total = rest + psu[1]
                            if total > budget:
                                continue
                            s = score(purpose, cpu, gpu, ram[0], ssd[0], case[0])
                            items = [(cpu, cpu_p, cpu_s, cpu_hi), board, ram, (gpu, gpu_p, gpu_s, gpu_hi), ssd, psu, case]
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


MODES = {
    "full": "🖥 Полная сборка под бюджет",
    "server": "🗄 Сервер (LLM / задачи)",
    "cpu_gpu": "🧩 Процессор к видеокарте",
    "gpu_cpu": "🧩 Видеокарта к процессору",
    "mb_cpu": "🔌 Материнская плата к процессору",
    "cpu_mb": "🔌 Процессор к материнской плате",
}
BASE_KIND = {"cpu_gpu": "gpu", "gpu_cpu": "cpu", "mb_cpu": "cpu", "cpu_mb": "mb"}
BASE_ASK = {"gpu": "видеокарту", "cpu": "процессор", "mb": "материнскую плату"}
RECOMMEND_ASK = {"cpu_gpu": "процессор", "gpu_cpu": "видеокарту",
                 "mb_cpu": "материнскую плату", "cpu_mb": "процессор"}
NEEDS_PURPOSE = {"full", "server", "cpu_gpu", "gpu_cpu"}
COND_LABEL = {"new": "🆕 новое (Ситилинк — диапазон цен)",
              "used": "♻️ б/у с Авито (SSD и БП — новые)"}
REF_RAM, REF_SSD, REF_CASE = BY_ID["d4_32"], BY_ID["ssd_1000"], BY_ID["case_2"]


def compatible_boards(cpu: Part) -> list[Part]:
    return [b for b in BY_KIND["mb"]
            if b.attrs["socket"] == cpu.attrs["socket"] and b.attrs["tier"] >= cpu.attrs["tier"]]


def cooler_for(cpu: Part, condition: str, st: PriceStore):
    """(ok, (part, (price, src)) | None): кулер, если процессору нужен отдельный."""
    need = cpu.attrs["cooler"]
    if not need:
        return True, None
    opts = [(c, st.get(c, condition)) for c in consumer_parts("cooler")
            if c.attrs["tier"] >= need]
    opts = [(c, pr) for c, pr in opts if pr]
    if not opts:
        return False, None
    return True, min(opts, key=lambda x: x[1][0])


def platform(cpu: Part, condition: str, st: PriceStore) -> dict | None:
    """Процессор + нужный кулер + самая дешёвая совместимая плата + 16 ГБ ОЗУ."""
    pr = st.get(cpu, condition)
    ok, cooler = cooler_for(cpu, condition, st)
    if not pr or not ok:
        return None
    best = None
    for b in compatible_boards(cpu):
        bp = st.get(b, condition)
        rams = [(r, st.get(r, condition)) for r in consumer_parts("ram")
                if r.attrs["type"] == b.attrs["ram"] and r.attrs["gb"] == 16]
        rams = [(r, p) for r, p in rams if p]
        if not bp or not rams:
            continue
        r, rp = rams[0]
        cost = bp[0] + rp[0]
        if best is None or cost < best[0]:
            best = (cost, b, r)
    if best is None:
        return None
    total = pr[0] + best[0] + (cooler[1][0] if cooler else 0)
    return {"cpu": pr, "total": total, "board": best[1], "ram": best[2], "cooler": cooler}


def psu_need(gpu: Part, cpu: Part, fixed: bool = False) -> float:
    load = gpu.attrs["tdp"] + 1.5 * cpu.attrs["tdp"]
    return load + PSU_FIXED if fixed else (load + PSU_BASE) * PSU_MULT


def psu_for(gpu: Part, cpu: Part, condition: str, st: PriceStore):
    need = psu_need(gpu, cpu)
    opts = [(p, st.get(p, condition)) for p in consumer_parts("psu")
            if p.attrs["watts"] >= need]
    opts = [(p, pr) for p, pr in opts if pr]
    return min(opts, key=lambda x: x[1][0], default=None)


# ---------------------------------------------------------------------------
# Серверные сборки: LLM / базовые задачи / тяжёлые задачи
# ---------------------------------------------------------------------------

SERVER_MIN_VRAM = {"server_base": 6, "server_llm": 12, "server_heavy": 24}
SERVER_MIN_RAM = {"server_base": 32, "server_llm": 32, "server_heavy": 64}
SERVER_VRAM_HINTS = [
    (12, "до ~13B параметров в Q4"),
    (16, "до ~20B параметров в Q4"),
    (22, "до ~30B параметров в Q4"),
    (24, "до ~32B в Q4; 70B — с выгрузкой части слоёв в ОЗУ"),
    (32, "до ~40B в Q4"),
    (48, "70B в Q4 или две модели по ~30B"),
    (64, "70B в Q5/Q6"),
    (80, "70B в Q8 или ~120B в Q4"),
    (96, "~120B в Q4"),
]


def psu_need_multi(gpus: list[Part], cpu: Part) -> float:
    """Запас мощности БП для одной или нескольких видеокарт."""
    load = sum(g.attrs["tdp"] for g in gpus) + 1.5 * cpu.attrs["tdp"]
    if len(gpus) == 1:
        return (load + PSU_BASE) * PSU_MULT
    # Для двух GPU не берём самый жёсткий коэффициент: 2×3090/4090
    # реально живут на качественном 1600 Вт, а не на 2000+ Вт.
    return (load + PSU_FIXED) * 1.15


def psu_for_gpus(gpus: list[Part], cpu: Part, condition: str, st: PriceStore):
    need = psu_need_multi(gpus, cpu)
    opts = [(p, st.get(p, condition)) for p in BY_KIND["psu"] if p.attrs["watts"] >= need]
    opts = [(p, pr) for p, pr in opts if pr]
    return min(opts, key=lambda x: x[1][0], default=None)


def server_cooler(cpu: Part, condition: str, st: PriceStore):
    """Для сервера кулер должен держать не только tier, но и TDP процессора."""
    if not cpu.attrs["cooler"]:
        return True, None
    need = cpu.attrs["cooler"]
    opts = []
    for c in BY_KIND["cooler"]:
        if c.attrs["tier"] < need or c.attrs["tdp"] < cpu.attrs["tdp"]:
            continue
        pr = st.get(c, condition)
        if pr:
            opts.append((c, pr))
    if not opts:
        return False, None
    return True, min(opts, key=lambda x: x[1][0])


def server_score(purpose: str, cpu: Part, gpus: list[Part], ram: Part, ssd: Part, case: Part) -> float:
    vram = sum(g.attrs["vram"] for g in gpus)
    gpu_perf = sum(g.attrs["perf"] for g in gpus)
    cuda = sum(1 for g in gpus if g.attrs["vendor"] == "nvidia")
    gb, ssd_gb = ram.attrs["gb"], ssd.attrs["gb"]
    ssd_bonus = {1000: 0, 2000: 4, 4000: 7}.get(ssd_gb, 0)

    if purpose == "server_base":
        # Файлы, Docker, БД, API: главное — CPU, ОЗУ и быстрый диск.
        return (1.1 * cpu.attrs["prod"] + 0.16 * gb + ssd_bonus
                + 0.08 * gpu_perf + 0.25 * case.attrs["bonus"])

    if purpose == "server_llm":
        # Инференс: суммарная VRAM важнее всего, потом скорость GPU и ОЗУ для загрузки модели.
        s = (1.9 * vram + 0.30 * gpu_perf + 0.12 * cpu.attrs["prod"]
             + 0.18 * gb + ssd_bonus + 4 * cuda + 0.2 * case.attrs["bonus"])
        return s * (0.97 if len(gpus) > 1 else 1.0)

    # Тяжёлые задачи: обучение/файнтюн, поэтому и VRAM, и скорость GPU, и CPU.
    return (1.5 * vram + 0.55 * gpu_perf + 0.45 * cpu.attrs["prod"]
            + 0.20 * gb + ssd_bonus + 3 * cuda + 0.2 * case.attrs["bonus"])


def server_options(purpose: str, condition: str, budget: int, store: PriceStore, top: int = 3) -> list[Build]:
    """Подбор сервера: 1–2 GPU, много ОЗУ, БП с запасом, корпус с продувом."""
    def avail(kind, server_only: bool = False):
        out = []
        for p in BY_KIND.get(kind, []):
            if server_only and not is_server(p):
                continue
            pr = store.get(p, condition)
            if pr:
                out.append((p, pr[0], pr[1], pr[2]))
        return sorted(out, key=lambda x: x[1])

    min_vram = SERVER_MIN_VRAM[purpose]
    min_ram = SERVER_MIN_RAM[purpose]
    cpus = avail("cpu", server_only=True)
    # Для двухкарточных сборок каждая карта может быть 12 ГБ, но суммарно
    # должно набраться min_vram (для «тяжёлых» — 24 ГБ).
    min_member = 12 if purpose != "server_base" else min_vram
    gpus = [g for g in avail("gpu") if g[0].attrs["vram"] >= min_member]
    boards = avail("mb", server_only=True)
    ssds = [s for s in avail("ssd") if s[0].attrs["gb"] >= 1000][:3]
    cases1 = [c for c in avail("case") if c[0].attrs["bonus"] >= 2][:4]
    cases2 = [c for c in avail("case") if c[0].attrs["bonus"] >= 4][:4]
    if purpose == "server_base":
        # Серверу базовых задач не нужна игровая видеокарта за 100+ тыс.
        gpus = [g for g in gpus if g[0].attrs["perf"] <= 45]
    if not (cpus and gpus and boards and ssds and cases1):
        return []

    all_ram = avail("ram", server_only=True)
    rams = {
        t: [r for r in all_ram if r[0].attrs["type"] == t and r[0].attrs["gb"] >= min_ram]
        for t in ("DDR4", "DDR5")
    }

    combos = [(g,) for g in gpus if g[0].attrs["vram"] >= min_vram]
    if purpose != "server_base":
        # Две одинаковые или разные карты: для LLM важен суммарный объём VRAM.
        for i, g1 in enumerate(gpus):
            for g2 in gpus[i:]:
                if (g1[0].attrs["vram"] >= 12 and g2[0].attrs["vram"] >= 12
                        and g1[0].attrs["vram"] + g2[0].attrs["vram"] >= min_vram):
                    combos.append((g1, g2))
    combos.sort(key=lambda combo: sum(g[1] for g in combo))

    cands = []
    for cpu, cpu_p, cpu_s, cpu_hi in cpus:
        ok, cooler = server_cooler(cpu, condition, store)
        if not ok:
            continue
        for rtype, ram_list in rams.items():
            if not ram_list:
                continue
            for n in (1, 2):
                if n == 2 and purpose == "server_base":
                    continue
                board = next((b for b in boards
                              if b[0].attrs["socket"] == cpu.attrs["socket"]
                              and b[0].attrs["ram"] == rtype
                              and b[0].attrs["tier"] >= cpu.attrs["tier"]
                              and b[0].attrs["gpu_slots"] >= n), None)
                if board is None:
                    continue
                case_opts = cases2 if n == 2 else cases1
                if not case_opts:
                    continue
                platform_cost = cpu_p + board[1] + (cooler[1][0] if cooler else 0)
                for combo in combos:
                    if len(combo) != n:
                        continue
                    gpu_cost = sum(g[1] for g in combo)
                    psu = psu_for_gpus([g[0] for g in combo], cpu, condition, store)
                    if not psu:
                        continue
                    psu_item = (psu[0], psu[1][0], psu[1][1], psu[1][2])
                    min_total = (platform_cost + gpu_cost + ram_list[0][1]
                                 + ssds[0][1] + case_opts[0][1] + psu_item[1])
                    if min_total > budget:
                        continue
                    fixed = platform_cost + gpu_cost
                    for ram in ram_list:
                        for ssd in ssds:
                            for case in case_opts:
                                total = fixed + ram[1] + ssd[1] + case[1] + psu_item[1]
                                if total > budget:
                                    continue
                                s = server_score(purpose, cpu, [g[0] for g in combo],
                                                 ram[0], ssd[0], case[0])
                                items = [(cpu, cpu_p, cpu_s, cpu_hi), board, ram]
                                items += list(combo)
                                items += [ssd, psu_item, case]
                                if cooler:
                                    items.insert(1, (cooler[0], cooler[1][0],
                                                     cooler[1][1], cooler[1][2]))
                                cands.append((s, -total, items))

    cands.sort(key=lambda x: (x[0], x[1]), reverse=True)
    builds, seen = [], set()
    for s, neg_total, items in cands:
        gpu_ids = tuple(i[0].id for i in items if i[0].kind == "gpu")
        key = (items[0][0].id, gpu_ids)
        if key in seen:
            continue
        seen.add(key)
        builds.append(Build([Item(*i) for i in items], -neg_total, round(s, 1)))
        if len(builds) == top:
            break
    return builds


def server_vram_hint(vram: int) -> str:
    hint = ""
    for threshold, text in SERVER_VRAM_HINTS:
        if vram >= threshold:
            hint = text
    return hint or "недостаточно VRAM для серьёзных моделей"


# ---------------------------------------------------------------------------
# Прогноз FPS: разбираем сборку CPU+GPU+RAM по относительным бенчмаркам
# ---------------------------------------------------------------------------

FPS_REF_PERF = 55  # RTX 4070 = 100% в таблице GPU, здесь её perf
FPS_GAMES = [
    {"name": "CS2", "ref": 380, "cpu_cap": 520, "cpu_w": 0.55,
     "res": {"1080p": 1.00, "1440p": 0.90}},
    {"name": "Bodycam", "ref": 90, "cpu_cap": 180, "cpu_w": 0.25,
     "res": {"1080p": 1.00, "1440p": 0.55}},
    {"name": "GTA 5", "ref": 150, "cpu_cap": 220, "cpu_w": 0.40,
     "res": {"1080p": 1.00, "1440p": 0.68}},
    {"name": "Cyberpunk 2077", "ref": 80, "cpu_cap": 160, "cpu_w": 0.15,
     "res": {"1080p": 1.00, "1440p": 0.62}},
]
FPS_QUICK = ("CS2", "Bodycam", "Cyberpunk 2077", "GTA 5")


def _fps_one(cpu: Part, gpu: Part, ram: Part, game: dict, res: str) -> int:
    """Один кадр/с: GPU-часть масштабируется от эталона, CPU-часть — от game-score."""
    gpu_fps = (game["ref"] * (gpu.attrs["perf"] / FPS_REF_PERF) ** 0.9
               * game["res"][res])
    vram_need = 6 if res == "1080p" else 9
    if gpu.attrs["vram"] < vram_need:
        gpu_fps *= 0.75  # не хватает видеопамяти — текстуры/настройки режутся

    cpu_fps = game["cpu_cap"] * (max(cpu.attrs["game"], 1) / 70) ** 1.1
    w = game["cpu_w"]
    fps = 1 / (w / max(cpu_fps, 1) + (1 - w) / max(gpu_fps, 1))
    if ram.attrs["gb"] < 16:
        fps *= 0.88
    return max(5, int(round(fps / 5) * 5))


def parse_fps(cpu: Part, gpu: Part, ram: Part) -> dict[str, dict[str, int]]:
    """Разбирает пару CPU+GPU и возвращает прогноз FPS по играм и разрешениям."""
    return {
        game["name"]: {res: _fps_one(cpu, gpu, ram, game, res)
                       for res in ("1080p", "1440p")}
        for game in FPS_GAMES
    }


def fps_short(cpu: Part, gpu: Part) -> str:
    data = parse_fps(cpu, gpu, REF_RAM)
    return (f"🎮 CS2 ~{data['CS2']['1080p']} · Cyberpunk 2077 "
            f"~{data['Cyberpunk 2077']['1080p']} FPS (1080p)")


def fps_summary(cpu: Part, gpu: Part, ram: Part) -> str:
    data = parse_fps(cpu, gpu, ram)
    lines = ["", "🎮 <b>Прогноз FPS</b> (1080p / 1440p, высокие настройки):"]
    for name in FPS_QUICK:
        lines.append(f"    {name}: ~{data[name]['1080p']} / ~{data[name]['1440p']}")
    lines.append("    <i>расчёт ±15%, без DLSS/FSR</i>")
    return "\n".join(lines)


def render_fps(b: Build, purpose: str, budget: int) -> str:
    cpu, gpu, ram = b.get("cpu"), b.get("gpu"), b.get("ram")
    if not (cpu and gpu and ram):
        return "😕 Для этой сборки не хватает данных для прогноза FPS."
    data = parse_fps(cpu, gpu, ram)
    lines = [
        "🎮 <b>Подробный прогноз FPS</b>",
        f"Цель: {PURPOSES.get(purpose, purpose)} · бюджет {rub(budget)}",
        f"CPU: <b>{html.escape(cpu.name)}</b>",
        f"GPU: <b>{html.escape(gpu.name)}</b> · ОЗУ {ram.attrs['gb']} ГБ",
        "",
    ]
    for game in FPS_GAMES:
        name = game["name"]
        lines.append(f"<b>{name}</b>: ~{data[name]['1080p']} FPS / ~{data[name]['1440p']} FPS")
    lines += [
        "",
        "<i>Как считается: берётся эталонная производительность GPU (RTX 4070), "
        "масштабируется под вашу карту и разрешение; затем учитывается потолок "
        "процессора через гармоническое среднее. Это прогноз, а не живой замер "
        "с benchmark-сайтов — у них нет стабильного API.</i>",
        "",
        "ℹ️ DLSS/FSR в режиме «Качество» обычно добавляет 30–60% кадров, "
        "трассировка лучей — наоборот, снижает.",
    ]
    return "\n".join(lines)


def pick_options(cands: list) -> list[tuple[str, tuple]]:
    """cands: (score, cost, part, info). Возвращает до трёх вариантов: бюджетно / оптимально / максимум."""
    if not cands:
        return []
    top = max(c[0] for c in cands)

    def cheapest(frac, exclude_ids=()):
        ok = [c for c in cands if c[0] >= frac * top and c[2].id not in exclude_ids]
        return min(ok, key=lambda c: (c[1], -c[0])) if ok else None

    maximum = max(cands, key=lambda c: (c[0], -c[1]))
    seen = {maximum[2].id}
    optimal = cheapest(0.86, seen)
    if optimal:
        seen.add(optimal[2].id)
    budget = cheapest(0.72, seen)

    picks = [("💸 Бюджетно", budget), ("⚖️ Оптимально", optimal), ("🚀 Максимум", maximum)]
    return [(label, c) for label, c in picks if c is not None]


def balance_note(cpu: Part, gpu: Part, purpose: str | None) -> str:
    if purpose not in ("game", "balance"):
        return ""
    if cpu.attrs["game"] < 0.75 * gpu.attrs["perf"]:
        return "\n    ⚠️ процессор будет ограничивать видеокарту"
    if gpu.attrs["perf"] < 0.55 * cpu.attrs["game"]:
        return "\n    ℹ️ видеокарта заметно слабее процессора — прирост даст апгрейд видеокарты"
    return ""


def price_range(lo: int, hi: int) -> str:
    if hi <= lo:
        return rub(lo)
    return f"{lo:,}".replace(",", " ") + " – " + rub(hi)


def price_txt(pr: tuple[int, str, int] | None) -> str:
    if not pr:
        return "цена неизвестна"
    est = pr[1] == "оценка"
    return f"{'~' if est else ''}{price_range(pr[0], pr[2])}"


def render_pair(d: dict, st: PriceStore) -> str:
    mode, cond, purpose = d["mode"], d["condition"], d.get("purpose")
    base = BY_ID[d["base"]]
    base_pr = st.get(base, cond)

    lines = [
        f"<b>{MODES[mode]}</b> · {COND_LABEL[cond]}",
        f"{KIND_RU[base.kind]}: <b>{html.escape(base.name)}</b>" + f" · {price_txt(base_pr)}"
        + (f" · {ALL_PURPOSES[purpose]}" if purpose else ""),
        "",
        f"Подбираем <b>{RECOMMEND_ASK[mode]}</b>…",
        "",
    ]
    estimated = bool(base_pr and base_pr[1] == "оценка")
    shown = False

    if mode == "cpu_gpu":
        # У пользователя уже есть видеокарта — подбираем процессор + платформу.
        gpu: Part = base
        gpu_pr = st.get(gpu, cond)
        estimated |= bool(gpu_pr and gpu_pr[1] == "оценка")
        cands = []
        for cpu in consumer_parts("cpu"):
            pl = platform(cpu, cond, st)
            if not pl:
                continue
            if purpose in ("game", "balance") and cpu.attrs["game"] < 0.4 * gpu.attrs["perf"]:
                continue  # процессор слишком слабый — будет жёсткое ограничение в играх
            if gpu_pr and pl["total"] > gpu_pr[0] * MAX_PRICE_RATIO:
                continue  # процессор+pлатформа слишком дороги по сравнению с видеокартой
            cands.append((score(purpose, cpu, gpu, REF_RAM, REF_SSD, REF_CASE),
                          pl["total"], cpu, pl))
        for label, (_, _, cpu, pl) in pick_options(cands):
            shown = True
            estimated |= pl["cpu"][1] == "оценка"
            extra = f", кулер {html.escape(pl['cooler'][0].name)}" if pl["cooler"] else ""
            together = pl["total"] + (gpu_pr[0] if gpu_pr else 0)
            lines += [f"<b>{label}</b>",
                      f"<b>Процессор:</b> {html.escape(cpu.name)} · {price_txt(pl['cpu'])}",
                      f"Платформа (плата {html.escape(pl['board'].name)}, "
                      f"{pl['ram'].attrs['type']} 16 ГБ{extra}): ≈ {rub(pl['total'])}",
                      f"С видеокартой вместе: ≈ {rub(together)}"]
            note = balance_note(cpu, gpu, purpose)
            if note:
                lines.append("    " + note.strip())
            lines += [f"    {fps_short(cpu, gpu)}", ""]

    elif mode == "gpu_cpu":
        # У пользователя уже есть процессор — подбираем видеокарту + БП.
        cpu: Part = base
        cpu_pr = st.get(base, cond)
        estimated |= bool(cpu_pr and cpu_pr[1] == "оценка")
        cands = []
        for gpu in consumer_parts("gpu"):
            pr = st.get(gpu, cond)
            psu = psu_for(gpu, cpu, cond, st)
            if not pr or not psu:
                continue
            if cpu_pr and pr[0] > cpu_pr[0] * MAX_PRICE_RATIO:
                continue  # видеокарта слишком дорога по сравнению с процессором
            if purpose in ("game", "balance"):
                if gpu.attrs["perf"] < 0.4 * cpu.attrs["game"]:
                    continue  # видеокарта слишком слабая для процессора
                if gpu.attrs["perf"] > 1.5 * cpu.attrs["game"] + 8:
                    continue  # видеокарта сильнее процессора в 1,5× — избыточно
            cands.append((score(purpose, cpu, gpu, REF_RAM, REF_SSD, REF_CASE),
                          pr[0] + psu[1][0], gpu, (pr, psu)))
        for label, (_, _, gpu, (pr, psu)) in pick_options(cands):
            shown = True
            estimated |= pr[1] == "оценка"
            add = pr[0] + psu[1][0]
            lines += [f"<b>{label}</b>",
                      f"<b>Видеокарта:</b> {html.escape(gpu.name)} · {price_txt(pr)}",
                      f"БП {psu[0].attrs['watts']} Вт: {html.escape(psu[0].name)} · "
                      f"{price_txt((psu[1][0], psu[1][1], psu[1][2]))}",
                      f"Дополнительно к процессору: ≈ {rub(add)}"]
            note = balance_note(cpu, gpu, purpose)
            if note:
                lines.append("    " + note.strip())
            if purpose in ("game", "balance"):
                lines += [f"    {fps_short(cpu, gpu)}"]
            lines.append("")

    elif mode == "mb_cpu":
        ok, cooler = cooler_for(base, cond, st)
        boards = sorted(((b, st.get(b, cond)) for b in compatible_boards(base)),
                        key=lambda x: x[1][0] if x[1] else 10**9)
        shown_tiers = set()
        for b, pr in boards:
            if not pr or (b.attrs["tier"], b.attrs["ram"]) in shown_tiers:
                continue
            shown_tiers.add((b.attrs["tier"], b.attrs["ram"]))
            shown = True
            estimated |= pr[1] == "оценка"
            label = ("💸 Минимально достаточная" if b.attrs["tier"] == base.attrs["tier"]
                     else "🚀 С запасом по питанию")
            lines += [f"<b>{label}</b>",
                      f"<b>Плата:</b> {html.escape(b.name)} · {price_txt(pr)}",
                      f"    Сокет {b.attrs['socket']} · {b.attrs['ram']} · уровень питания {b.attrs['tier']}", ""]
        if cooler:
            lines.append(f"Процессору нужно отдельное охлаждение: {html.escape(cooler[0].name)} · "
                         f"{price_range(cooler[1][0], cooler[1][2])}.")

    else:
        cands = []
        for cpu in consumer_parts("cpu"):
            if cpu.attrs["socket"] != base.attrs["socket"] or cpu.attrs["tier"] > base.attrs["tier"]:
                continue
            pr, (ok, cooler) = st.get(cpu, cond), cooler_for(cpu, cond, st)
            if not pr or not ok:
                continue
            cost = pr[0] + (cooler[1][0] if cooler else 0)
            cands.append((cpu, pr, cost))
        picks = {"💸 Дешевле всего": min(cands, key=lambda c: c[2], default=None),
                 "🎮 Лучший для игр": max(cands, key=lambda c: (c[0].attrs["game"], -c[2]), default=None),
                 "💼 Лучший для работы": max(cands, key=lambda c: (c[0].attrs["prod"], -c[2]), default=None),
                 "⚖️ Лучшее соотношение": max(cands, key=lambda c: (c[0].attrs["game"] + c[0].attrs["prod"]) / c[2],
                                              default=None)}
        merged: dict[str, tuple] = {}
        for label, c in picks.items():
            if c:
                merged.setdefault(c[0].id, (c, []))[1].append(label)
        for c, labels in merged.values():
            shown = True
            cpu, pr, cost = c
            estimated |= pr[1] == "оценка"
            lines += [f"<b>{' · '.join(labels)}</b>",
                      f"<b>Процессор:</b> {html.escape(cpu.name)} · {price_txt(pr)}"
                      + ("\n    + отдельный кулер в комплект не входит" if cpu.attrs["cooler"] else ""), ""]
        if merged:
            lines.append(f"Память для этой платы: {base.attrs['ram']}.")

    if not shown:
        lines.append("😕 Не нашёл подходящих вариантов в этом режиме.")
    if estimated:
        lines.append("\n<i>~ — оценочная цена: живая цена пока не получена.</i>")
    if cond == "used":
        lines.append("\n⚠️ <i>Б/у: проверяйте комплектующие и берите через «Авито Доставку».</i>")
    return "\n".join(lines)


def render_server(b: Build, purpose: str, condition: str, budget: int, idx: int, total_variants: int) -> str:
    mode = ("🆕 новое, Ситилинк (диапазон цен)" if condition == "new"
            else "♻️ б/у с Авито (SSD и БП — новые)")
    lines = [f"<b>{SERVER_PURPOSES[purpose]}</b> · {mode}",
             f"Бюджет: <b>{rub(budget)}</b> · вариант {idx + 1}/{total_variants}", ""]
    estimated = False
    for kind in ORDER:
        items = [i for i in b.items if i.part.kind == kind]
        if not items:
            continue
        if kind == "gpu" and len(items) > 1:
            lo = sum(i.price for i in items)
            hi = sum(i.hi for i in items)
            est = any(i.src == "оценка" for i in items)
            estimated |= est
            names = " + ".join(html.escape(i.part.name) for i in items)
            lines.append(f"<b>Видеокарты ({len(items)}):</b> {names}\n"
                         f"    {'~' if est else ''}{price_range(lo, hi)} <i>(сумма)</i>")
            continue
        for it in items:
            est = it.src == "оценка"
            estimated |= est
            lines.append(f"<b>{KIND_RU[kind]}:</b> {html.escape(it.part.name)}\n"
                         f"    {'~' if est else ''}{price_range(it.price, it.hi)} <i>({it.src})</i>")

    total_hi = sum(i.hi for i in b.items)
    lines += ["", f"<b>Итого: {price_range(b.total, total_hi)}</b>"
                  f" · остаток от минимальной цены {rub(budget - b.total)}"]

    gpus = b.get_all("gpu")
    if gpus:
        vram = sum(g.attrs["vram"] for g in gpus)
        lines.append(f"\n🧠 Суммарная VRAM: <b>{vram} ГБ</b> — {server_vram_hint(vram)}.")
        ram = b.get("ram")
        if purpose == "server_llm" and ram:
            if ram.attrs["gb"] < vram:
                lines.append("    ⚠️ ОЗУ меньше VRAM: загрузка больших моделей и offload будут тесными.")
            else:
                lines.append(f"    ОЗУ {ram.attrs['gb']} ГБ — достаточно для загрузки модели и ОС.")
            if ram.attrs["gb"] < 64:
                lines.append("    ℹ️ 32 ГБ хватит для моделей 7–14B; для 30B+ лучше 64 ГБ и больше.")
        if purpose == "server_heavy":
            lines.append("    Для обучения и файнтюна важны суммарная VRAM, скорость GPU и питание.")
        if len(gpus) > 1:
            lines.append("    ⚠️ Две видеокарты: нужны 2 кабеля питания на каждую, просторный корпус и продув.")

    if estimated:
        lines.append("\n<i>~ — оценочная цена: живая цена с Ситилинка/Авито пока не получена.</i>")
    if condition == "used":
        lines.append("\n⚠️ <i>Б/у: для LLM особенно проверяйте VRAM-тест, температуры и следы ремонта. "
                     "Берите через «Авито Доставку» с проверкой.</i>")
    return "\n".join(lines)


store = PriceStore()
dp = Dispatcher()


class Flow(StatesGroup):
    budget = State()


def kb(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t, callback_data=d) for t, d in row] for row in rows
    ])


def purpose_kb(back: str = "nav:mode", mode: str = "full"):
    source = SERVER_PURPOSES if mode == "server" else PURPOSES
    items = [(name, f"p:{key}") for key, name in source.items()]
    return kb([items[:2], items[2:], [("⬅️ Назад", back)]])


def condition_kb(back: str = "nav:purpose"):
    return kb([
        [("🆕 Новое (магазины)", "c:new"), ("♻️ Б/у (Авито)", "c:used")],
        [("⬅️ Назад", back)],
    ])


def mode_kb():
    items = [(name, f"m:{key}") for key, name in MODES.items()]
    return kb([items[i:i + 2] for i in range(0, len(items), 2)])


def base_kb(mode: str, page: int = 0):
    parts = consumer_parts(BASE_KIND[mode])
    page_size = 8
    total_pages = max(1, (len(parts) + page_size - 1) // page_size)
    page = max(0, min(page, total_pages - 1))
    chunk = parts[page * page_size:(page + 1) * page_size]
    rows = [[(p.name, f"s:{p.id}") for p in chunk[i:i + 2]] for i in range(0, len(chunk), 2)]
    nav = []
    if page > 0:
        nav.append(("‹", f"bp:{mode}:{page - 1}"))
    if total_pages > 1:
        nav.append((f"{page + 1}/{total_pages}", f"bp:{mode}:{page}"))
    if page < total_pages - 1:
        nav.append(("›", f"bp:{mode}:{page + 1}"))
    rows.append(nav)
    rows.append([("⬅️ Назад", "nav:mode")])
    return kb(rows)


def budget_kb(presets: list[int] | None = None):
    if presets is None:
        presets = [50_000, 70_000, 100_000, 150_000, 250_000]
    btns = [(f"{p // 1000}к", f"b:{p}") for p in presets]
    return kb([btns[:3], btns[3:], [("⌨️ Ввести сумму текстом", "nav:budget")],
               [("⬅️ Назад", "nav:condition")]])


def result_kb(n: int, idx: int, show_fps: bool = False):
    variants = [(("• " if i == idx else "") + f"Вариант {i + 1}", f"v:{i}") for i in range(n)]
    rows = [variants]
    if show_fps:
        rows.append([("📊 FPS подробнее", f"f:{idx}")])
    rows += [[("💰 Другой бюджет", "nav:budget"), ("🎯 Другая цель", "nav:purpose")],
             [("🏠 В начало", "nav:mode")]]
    return kb(rows)


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
    22: "модели до ~30B (Q4)",
    24: "модели до ~32–34B (Q4), 70B — с выгрузкой в ОЗУ",
    32: "модели до ~40B (Q4)",
    48: "70B (Q4) или две модели по ~30B",
}


def render(b: Build, purpose: str, condition: str, budget: int, idx: int, total_variants: int) -> str:
    mode = ("🆕 новое, Ситилинк (диапазон цен)" if condition == "new"
            else "♻️ б/у с Авито (SSD и БП — новые)")
    lines = [f"<b>{PURPOSES[purpose]}</b> · {mode}",
             f"Бюджет: <b>{rub(budget)}</b> · вариант {idx + 1}/{total_variants}", ""]
    estimated = False
    for kind in ORDER:
        for it in (i for i in b.items if i.part.kind == kind):
            est = it.src == "оценка"
            estimated |= est
            lines.append(f"<b>{KIND_RU[kind]}:</b> {html.escape(it.part.name)}\n"
                         f"    {'~' if est else ''}{price_range(it.price, it.hi)} <i>({it.src})</i>")
    total_hi = sum(i.hi for i in b.items)
    total_line = f"<b>Итого: {price_range(b.total, total_hi)}</b>"
    lines += ["", total_line + f" · остаток от минимальной цены {rub(budget - b.total)}"]

    gpu = b.get("gpu")
    if gpu:
        vram = gpu.attrs["vram"]
        if purpose == "llm":
            lines.append(f"\n🧠 VRAM {vram} ГБ — {LLM_HINTS.get(vram, '')}.")
        elif vram <= 8 and budget >= 100_000:
            lines.append("\nℹ️ У видеокарты всего 8 ГБ памяти — для новых игр в высоком качестве маловато.")
        if purpose in ("game", "balance"):
            cpu, ram = b.get("cpu"), b.get("ram")
            if cpu and ram:
                lines.append(fps_summary(cpu, gpu, ram))
    if estimated:
        lines.append("\n<i>~ — оценочная цена: живая цена с Ситилинка/Авито пока не получена.</i>")
    if condition == "used":
        lines.append("\n⚠️ <i>Б/у: проверяйте видеокарту (майнинг, ремонт), просите тесты "
                     "и берите через «Авито Доставку» с проверкой.</i>")
    return "\n".join(lines)


async def show_result(target: Message, state: FSMContext, idx: int = 0, edit: bool = False):
    d = await state.get_data()
    if not {"purpose", "condition", "budget"} <= d.keys():
        text = "Сессия устарела, начните заново: /start"
        markup = kb([ [("🏠 В начало", "nav:mode")] ])
        await (target.edit_text if edit else target.answer)(text, reply_markup=markup)
        return
    is_server = d.get("purpose") in SERVER_PURPOSES
    builder = server_options if is_server else build_options
    builds = await asyncio.to_thread(builder, d["purpose"], d["condition"], d["budget"], store)
    if not builds:
        text = ("😕 На эту сумму не получилось собрать совместимую сборку.\n"
                "Попробуйте увеличить бюджет или выбрать другой режим.")
        if is_server:
            text = ("😕 Под этот серверный бюджет совместимая сборка не складывается.\n"
                    "Попробуйте сумму побольше или вариант «Базовые задачи».")
        markup = kb([[("💰 Другой бюджет", "nav:budget"), ("🎯 Другая цель", "nav:purpose")],
                     [("🏠 В начало", "nav:mode")]])
    else:
        idx = min(idx, len(builds) - 1)
        renderer = render_server if is_server else render
        text = renderer(builds[idx], d["purpose"], d["condition"], d["budget"], idx, len(builds))
        show_fps = (not is_server) and d["purpose"] in ("game", "balance")
        markup = result_kb(len(builds), idx, show_fps)
    await (target.edit_text if edit else target.answer)(text, reply_markup=markup)


async def show_pair(target: Message, state: FSMContext, edit: bool = False):
    d = await state.get_data()
    text = await asyncio.to_thread(render_pair, d, store)
    row = [("🔁 Другой выбор", "nav:base")]
    if d["mode"] in NEEDS_PURPOSE:
        row.append(("🎯 Другая цель", "nav:purpose"))
    markup = kb([row, [("🏠 В начало", "nav:mode")]])
    await (target.edit_text if edit else target.answer)(text, reply_markup=markup)


MODE_TEXT = (
    "👋 Привет! Я подберу комплектующие для ПК или сервера.\n\n"
    "Цены: 🆕 новое — Ситилинк, ♻️ б/у — Авито (SSD и БП только новые), "
    "показываю диапазон цен.\n"
    f"Бюджет: от {rub(MIN_BUDGET)} (сервер — от {rub(SERVER_MIN_BUDGET)}).\n\n"
    "<b>Что подбираем?</b>"
)


def purpose_text(d: dict) -> str:
    if d.get("mode") == "server":
        return ("<b>Какой сервер собираем?</b>\n\n"
                "🗄 Базовые задачи — файлы, Docker, БД, API.\n"
                "🧠 LLM — инференс моделей, чем больше VRAM, тем крупнее модель.\n"
                "🔥 Тяжёлые задачи — обучение/файнтюн; реалистично от ~250 000 ₽.")
    if d.get("mode") == "full":
        return "<b>Для чего собираем ПК?</b>"
    base = BY_ID[d["base"]]
    return f"{KIND_RU[base.kind]}: <b>{html.escape(base.name)}</b>\n\n<b>Под какую задачу?</b>"


def base_text(mode: str) -> str:
    return f"<b>{MODES[mode]}</b>\n\nВыберите {BASE_ASK[BASE_KIND[mode]]}:"


def condition_text(d: dict) -> str:
    mode = d.get("mode", "full")
    if mode == "server":
        head = f"{MODES['server']}\n{ALL_PURPOSES[d['purpose']]}"
    elif mode == "full":
        head = PURPOSES[d["purpose"]]
    else:
        head = f"{MODES[d['mode']]}\n{html.escape(BY_ID[d['base']].name)}"
        if d.get("purpose"):
            head += f" · {ALL_PURPOSES[d['purpose']]}"
    return f"{head}\n\n<b>Какие комплектующие берём?</b>"


def condition_back(d: dict) -> str:
    return "nav:purpose" if d.get("mode", "full") in NEEDS_PURPOSE else "nav:base"


@dp.message(CommandStart())
@dp.message(Command("new"))
async def command_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(MODE_TEXT, reply_markup=mode_kb())


@dp.callback_query(F.data == "nav:mode")
async def nav_mode(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.edit_text(MODE_TEXT, reply_markup=mode_kb())
    await cb.answer()


@dp.callback_query(F.data.startswith("m:"))
async def choose_mode(cb: CallbackQuery, state: FSMContext):
    mode = cb.data[2:]
    if mode not in MODES:
        return await cb.answer()
    await state.clear()
    await state.update_data(mode=mode)
    if mode in ("full", "server"):
        await cb.message.edit_text(purpose_text({"mode": mode}), reply_markup=purpose_kb("nav:mode", mode))
    else:
        await cb.message.edit_text(base_text(mode), reply_markup=base_kb(mode))
    await cb.answer()


@dp.callback_query(F.data == "nav:base")
async def nav_base(cb: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    if d.get("mode") not in BASE_KIND:
        return await nav_mode(cb, state)
    await state.clear()
    await cb.message.edit_text(base_text(d["mode"]), reply_markup=base_kb(d["mode"]))
    await cb.answer()


@dp.callback_query(F.data.startswith("bp:"))
async def base_page(cb: CallbackQuery, state: FSMContext):
    _, mode, page = cb.data.split(":")
    d = await state.get_data()
    if d.get("mode") != mode or mode not in BASE_KIND:
        return await cb.answer()
    await cb.message.edit_text(base_text(d["mode"]), reply_markup=base_kb(mode, int(page)))
    await cb.answer()


@dp.callback_query(F.data.startswith("s:"))
async def choose_base(cb: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    mode, part = d.get("mode"), BY_ID.get(cb.data[2:])
    if mode not in BASE_KIND or part is None or part.kind != BASE_KIND[mode]:
        return await nav_mode(cb, state)
    await state.update_data(base=part.id)
    d = await state.get_data()
    if mode in NEEDS_PURPOSE:
        await cb.message.edit_text(purpose_text(d), reply_markup=purpose_kb("nav:base", mode))
    else:
        await cb.message.edit_text(condition_text(d), reply_markup=condition_kb(condition_back(d)))
    await cb.answer()


@dp.callback_query(F.data == "nav:purpose")
async def nav_purpose(cb: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    mode = d.get("mode")
    if not mode or (mode in BASE_KIND and "base" not in d):
        return await nav_mode(cb, state)
    await state.clear()
    back = "nav:mode" if mode in ("full", "server") else "nav:base"
    await cb.message.edit_text(purpose_text(d), reply_markup=purpose_kb(back, mode))
    await cb.answer()


@dp.callback_query(F.data.startswith("p:"))
async def choose_purpose(cb: CallbackQuery, state: FSMContext):
    purpose = cb.data.split(":")[1]
    d = await state.get_data()
    mode = d.get("mode")
    allowed = SERVER_PURPOSES if mode == "server" else PURPOSES
    if purpose not in allowed or mode not in NEEDS_PURPOSE:
        return await cb.answer()
    await state.update_data(purpose=purpose)
    d = await state.get_data()
    await cb.message.edit_text(condition_text(d), reply_markup=condition_kb(condition_back(d)))
    await cb.answer()


@dp.callback_query(F.data == "nav:condition")
async def nav_condition(cb: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    if d.get("mode") not in ("full", "server") or "purpose" not in d:
        return await nav_purpose(cb, state)
    await state.clear()
    await cb.message.edit_text(condition_text(d), reply_markup=condition_kb(condition_back(d)))
    await cb.answer()


async def ask_budget(message: Message, state: FSMContext):
    d = await state.get_data()
    is_server = d.get("purpose") in SERVER_PURPOSES
    minimum = SERVER_MIN_BUDGET if is_server else MIN_BUDGET
    presets = SERVER_BUDGET_PRESETS if is_server else None
    await state.set_state(Flow.budget)
    await message.edit_text(
        f"<b>Какой бюджет?</b>\nВыберите кнопкой или напишите сумму "
        f"(от {rub(minimum)}), например <code>85000</code> или <code>85к</code>.",
        reply_markup=budget_kb(presets))


@dp.callback_query(F.data.startswith("c:"))
async def choose_condition(cb: CallbackQuery, state: FSMContext):
    cond = cb.data.split(":")[1]
    if cond not in ("new", "used"):
        return await cb.answer()
    d = await state.get_data()
    mode = d.get("mode")
    if mode is None or (mode in BASE_KIND and "base" not in d) or (mode in NEEDS_PURPOSE and "purpose" not in d):
        return await nav_mode(cb, state)
    await state.update_data(condition=cond)
    if mode in ("full", "server"):
        await ask_budget(cb.message, state)
    else:
        await cb.message.edit_text("⏳ Подбираю…")
        await show_pair(cb.message, state, edit=True)
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
    value = int(cb.data.split(":")[1])
    is_server = d.get("purpose") in SERVER_PURPOSES
    lo, hi = (SERVER_MIN_BUDGET, SERVER_MAX_BUDGET) if is_server else (MIN_BUDGET, MAX_BUDGET)
    if not lo <= value <= hi:
        return await ask_budget(cb.message, state)
    await state.update_data(budget=value)
    await cb.message.edit_text("⏳ Подбираю…")
    await show_result(cb.message, state, edit=True)
    await cb.answer()


@dp.message(Flow.budget, F.text)
async def choose_budget_text(message: Message, state: FSMContext):
    budget = parse_budget(message.text)
    if budget is None:
        return await message.answer("Не понял сумму 🤔 Напишите числом, например <code>85000</code> или <code>85к</code>.")
    d = await state.get_data()
    is_server = d.get("purpose") in SERVER_PURPOSES
    lo, hi = (SERVER_MIN_BUDGET, SERVER_MAX_BUDGET) if is_server else (MIN_BUDGET, MAX_BUDGET)
    if budget < lo:
        return await message.answer(f"Минимальный бюджет — {rub(lo)}. Введите сумму побольше.")
    if budget > hi:
        return await message.answer(f"Давайте не больше {rub(hi)} 🙂")
    await state.update_data(budget=budget)
    await show_result(message, state)


@dp.callback_query(F.data.startswith("v:"))
async def switch_variant(cb: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    if not {"purpose", "condition", "budget"} <= d.keys():
        return await cb.answer("Сессия устарела, начните заново: /start", show_alert=True)
    await show_result(cb.message, state, idx=int(cb.data.split(":")[1]), edit=True)
    await cb.answer()


@dp.callback_query(F.data.startswith("f:"))
async def show_fps_details(cb: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    if not {"purpose", "condition", "budget"} <= d.keys():
        return await cb.answer("Сессия устарела, начните заново: /start", show_alert=True)
    idx = int(cb.data.split(":")[1])
    builds = await asyncio.to_thread(build_options, d["purpose"], d["condition"], d["budget"], store)
    if not builds:
        return await cb.answer("Сборка не найдена, выберите бюджет заново", show_alert=True)
    idx = min(idx, len(builds) - 1)
    text = render_fps(builds[idx], d["purpose"], d["budget"])
    await cb.message.answer(text, reply_markup=kb([[("⬅️ К сборке", f"v:{idx}")]]))
    await cb.answer()


@dp.message()
async def fallback(message: Message):
    await message.answer("Нажмите /start, чтобы подобрать сборку.")


async def main():
    token = ""
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