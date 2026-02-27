#!/usr/bin/env python3
"""
Import Category Assignments from MP_CLS.fpb
Anson Supermart Catalog Management

Maps each product's CLRKEY (via MERCH_MASTER.csv) to a category_id in the DB.
Only updates products that are currently uncategorized (category_id NULL or 0).

Usage:
  python import_cls_categories.py --dry-run   # Preview
  python import_cls_categories.py             # Apply
"""

import struct
import csv
import sqlite3
import argparse
import sys
from datetime import datetime
from pathlib import Path

DB_PATH       = Path(__file__).parent / "anson_products.db"
CSV_PATH      = Path(__file__).parent.parent / "Data" / "MERCH_MASTER.csv"
FPB_PATH      = Path(__file__).parent.parent / "Data" / "MP_CLS.fpb"
BATCH_COMMIT  = 500

# ---------------------------------------------------------------------------
# CLRKEY -> category_id mapping
# Category IDs from the categories table in the DB.
# ---------------------------------------------------------------------------

CLRKEY_TO_CAT = {
    # ---- Bread Garden -------------------------------------------------------
    "500000": 11,   # BREAD GARDEN            -> Bread
    "501000": 11,   # BG.BREAD                -> Bread
    "501010": 11,   # BG.BR.BREAD             -> Bread
    "501100":  6,   # BG.PASTRIES             -> Baking
    "501110":  6,   # BG.PS.PASTRIES          -> Baking
    "501111":  6,   # BG.PS.CAKE              -> Baking
    "501200": 11,   # BG.BREAD SPECIALTIES    -> Bread
    "501210": 11,   # BG.BS.CROISSANT         -> Bread
    "501211": 11,   # PIZZA PIE BRD GRN       -> Bread
    "501112":  6,   # BG.PS.CAKE DECORATION   -> Baking
    "501212": 59,   # BG.BS.RICEMEAL          -> Rice, Noodles, Pasta

    # ---- Essential: Dairy (101xxx) ------------------------------------------
    "100000": 15,   # ESSENTIAL PRODUCTS      -> Cereals (fallback)
    "101000": 47,   # ES.DAIRY PRODUCTS       -> Milk
    "101010": 42,   # ES.DP.INFANT MILK       -> Infant Milk
    "101011": 47,   # ES.DP.LIQUID MILK-CANNED-> Milk
    "101012": 47,   # ES.DP.RTD MILK          -> Milk
    "101013": 47,   # ES.DP.LIQUID MILK-BOTTLED-> Milk
    "101014": 16,   # ES.DP.CHEESE            -> Cheese
    "101015": 12,   # ES.DP.BUTTER            -> Butter & Fresh Cream
    "101016": 67,   # ES.DP.YOGURT            -> Yogurt
    "101017": 23,   # ES.DP.CREAMS            -> Culinary Milk & Cream
    "101018": 21,   # ES.DP.COFFEE CREAMER    -> Coffee & Creamer
    "101019": 47,   # ES.DP.FULL CREAM POWDER -> Milk
    "101020": 47,   # ES.DP.NON-FAT POWDER    -> Milk
    "101021": 47,   # ES.DP.NON-FAT LIQ BOT  -> Milk
    "101022": 47,   # ES.DP.NON-FAT LIQ TETRA -> Milk
    "101023": 47,   # ES.DP.INST MILK POWDER  -> Milk
    "101024": 42,   # ES.DP.GROWING-UP FORMULA-> Infant Milk
    "101026": 41,   # ES.DP.ICE CREAM         -> Ice Cream
    "101027": 42,   # ES.DP.MATERNAL FORMULA  -> Infant Milk
    "101028": 19,   # ES.DP.CHOCOLATE DRINKS  -> Chocolate Drinks
    "101029": 13,   # ES.DP.CANDIES           -> Candy & Chocolates
    "101030": 22,   # ES.DP.SNACKS            -> Cookies/Biscuits/Crackers
    "101031": 13,   # ES.DP.CHOCOLATES        -> Candy & Chocolates
    "101032": 60,   # ES.DP.SALAD AIDS/SPREAD -> Sauce, Spices, Spreads
    "101033": 15,   # ES.DP.CEREALS           -> Cereals
    "101034": 60,   # ES.DP.SYRUPS            -> Sauce, Spices, Spreads
    "101035": 60,   # ES.DP.PEANUT BUTTER     -> Sauce, Spices, Spreads
    "101036": 60,   # ES.DP.COCO JAM          -> Sauce, Spices, Spreads
    "101037": 28,   # ES.DP.BABY CEREAL MIX   -> Feeding & Nursing
    "101038": 60,   # ES.DP.GULAMAN           -> Sauce, Spices, Spreads

    # ---- Essential: Agricultural (102xxx) -----------------------------------
    "102000": 60,   # ES.AGRICULTURAL         -> Sauce, Spices, Spreads
    "102010": 60,   # ES.AP.SUGAR             -> Sauce, Spices, Spreads
    "102011": 60,   # ES.AP.SALT              -> Sauce, Spices, Spreads
    "102012": 59,   # ES.AP.RICE              -> Rice, Noodles, Pasta
    "102013": 60,   # ES.AP.SPICES            -> Sauce, Spices, Spreads
    "102014":  6,   # ES.AP.FLOUR             -> Baking
    "102015": 21,   # ES.AP.COFFEE            -> Coffee & Creamer
    "102016": 33,   # ES.AP.VEGETABLES        -> Fresh Vegetables
    "102017": 36,   # ES.AP.FRUITS            -> Fruits
    "102018": 60,   # ES.AP.VINEGAR           -> Sauce, Spices, Spreads
    "102019": 60,   # ES.AP.SOY SAUCE         -> Sauce, Spices, Spreads
    "102020": 60,   # ES.AP.PATIS             -> Sauce, Spices, Spreads
    "102021": 60,   # ES.AP.BAGOONG           -> Sauce, Spices, Spreads
    "102022": 60,   # ES.AP.BANANA CATSUP     -> Sauce, Spices, Spreads
    "102023": 60,   # ES.AP.TOMATO CATSUP     -> Sauce, Spices, Spreads
    "102024": 60,   # ES.AP.TOMATO SAUCE      -> Sauce, Spices, Spreads
    "102025":  6,   # ES.AP.COCOA POWDER      -> Baking
    "102026": 60,   # ES.AP.SAUCES-LIQUID     -> Sauce, Spices, Spreads
    "102027": 60,   # ES.AP.MANGO CATSUP      -> Sauce, Spices, Spreads
    "102028": 15,   # ES.AP.OATMEAL           -> Cereals
    "102029": 43,   # ES.AP.INSTANT NOODLES   -> Instant Noodles
    "102030": 47,   # ES.AP.SOYA MILK POWDER  -> Milk
    "102031": 47,   # ES.AP.SOYA MILK LIQUID  -> Milk
    "102032": 60,   # ES.AP.SOYABEAN PRODUCTS -> Sauce, Spices, Spreads

    # ---- Essential: Marine & Fresh Water (103xxx) ---------------------------
    "103000": 32,   # ES.MARINE PRODUCTS      -> Fresh Seafood
    "103010": 32,   # ES.MP.FISH              -> Fresh Seafood

    # ---- Essential: Fresh Meat (104xxx) -------------------------------------
    "104000": 58,   # ES.FRESH MEAT           -> Pork (fallback)
    "104010": 58,   # ES.FM.PORK              -> Pork
    "104011":  8,   # ES.FM.BEEF              -> Beef
    "104012": 17,   # ES.FM.POULTRY           -> Chicken

    # ---- Essential: Detergent/Laundry (105xxx) ------------------------------
    "105000": 46,   # ES.DETERGENT/LAUNDRY    -> Laundry Items
    "105010": 46,   # ES.DS.DETERGENT BAR     -> Laundry Items
    "105011": 46,   # ES.DS.DETERGENT POWDER  -> Laundry Items
    "105012": 46,   # ES.DS.DETERGENT LIQUID  -> Laundry Items
    "105013": 40,   # ES.DS.DISHWASHING SOAP  -> Household Cleaners
    "105014": 40,   # ES.DS.CLEANSER          -> Household Cleaners
    "105015": 46,   # ES.DS.FABRIC BLEACH     -> Laundry Items

    # ---- Essential: Processed/Preserved Foods (106xxx) ----------------------
    "106000": 14,   # ES.PROCESSED FOODS      -> Canned Goods
    "106010": 14,   # ES.PP.CANNED MEAT       -> Canned Goods
    "106011": 14,   # ES.PP.CANNED BEANS      -> Canned Goods
    "106012": 14,   # ES.PP.CANNED VEGETABLES -> Canned Goods
    "106013": 14,   # ES.PP.CANNED FRUITS     -> Canned Goods
    "106014": 24,   # ES.PP.HOTDOGS           -> Deli & Cold Cuts
    "106015": 24,   # ES.PP.HAM               -> Deli & Cold Cuts
    "106016": 24,   # ES.PP.BACON             -> Deli & Cold Cuts
    "106017": 24,   # ES.PP.LONGANISA         -> Deli & Cold Cuts
    "106018": 24,   # ES.PP.TOCINO            -> Deli & Cold Cuts
    "106019": 35,   # ES.PP.FROZEN PEAS       -> Frozen Vegetables
    "106020": 74,   # ES.PP.SHRIMP/SQUID BALLS-> Frozen Meat & Poultry
    "106021": 14,   # ES.PP.DRIED SEAFOODS    -> Canned Goods
    "106022": 14,   # ES.PP.DRIED FRUITS      -> Canned Goods
    "106023": 14,   # ES.PP.PRESERVED FRUITS  -> Canned Goods
    "106024": 60,   # ES.PP.PICKLES           -> Sauce, Spices, Spreads
    "106025": 14,   # ES.PP.SARDINES/MACKEREL -> Canned Goods
    "106026": 14,   # ES.PP.TUNA              -> Canned Goods
    "106027": 14,   # ES.PP.CANNED SEAFOODS   -> Canned Goods
    "106028": 74,   # ES.PP.UNMEAT FROZEN     -> Frozen Meat & Poultry

    # ---- Essential: Cooking Oil (107xxx) ------------------------------------
    "107000": 48,   # ES.COOKING OIL          -> Oil
    "107010": 48,   # ES.CO.VEGETABLE OIL     -> Oil
    "107011": 48,   # ES.CO.SOYA OIL          -> Oil
    "107012": 48,   # ES.CO.CORN OIL          -> Oil
    "107013": 48,   # ES.CO.SUNFLOWER/CANOLA  -> Oil
    "107014": 48,   # ES.CO.OLIVE OIL         -> Oil
    "107015": 48,   # ES.CO.SESAME OIL        -> Oil
    "107016": 48,   # ES.CO.VIRGIN COCO OIL   -> Oil

    # ---- Essential: School Supplies (108xxx) --------------------------------
    "108000": 53,   # ES.SCHOOL SUPPLIES      -> Other Supplies
    "108010": 54,   # ES.SS.NOTEBOOKS         -> Paper & Notebook
    "108011": 54,   # ES.SS.PAD PAPER         -> Paper & Notebook
    "108012": 55,   # ES.SS.PENS              -> Pens & Markers
    "108013": 55,   # ES.SS.PENCILS           -> Pens & Markers
    "108014": 53,   # ES.SS.ERASERS           -> Other Supplies
    "108015": 53,   # ES.SS.PASTE/GLUE        -> Other Supplies
    "108016": 53,   # ES.SS.SHARPENER         -> Other Supplies
    "108017": 53,   # ES.SS.RULER             -> Other Supplies
    "108018": 53,   # ES.SS.SCISSORS          -> Other Supplies
    "108019": 53,   # ES.SS.DICTIONARIES      -> Other Supplies
    "108020": 75,   # ES.SS.CRAYONS/DRAWING   -> Art Supplies
    "108021": 72,   # ES.SS.SCHOOL BAGS       -> Bags
    "108022": 53,   # ES.SS.BROWN ENVELOPE    -> Other Supplies
    "108023": 53,   # ES.SS.FOLDERS           -> Other Supplies
    "108024": 54,   # ES.SS.PAPER PRODUCTS    -> Paper & Notebook

    # ---- Essential: Insecticides/Pesticides (109xxx) ------------------------
    "109000": 56,   # ES.INSECTICIDES         -> Pest Control
    "109010": 56,   # ES.IP.INSECTICIDES      -> Pest Control
    "109011": 56,   # ES.IP.MOSQUITO COIL     -> Pest Control
    "109013": 40,   # ES.IP.MURIATIC ACID     -> Household Cleaners
    "109014": 40,   # ES.IP.NAPTHALENE BALL   -> Household Cleaners
    "109015": 56,   # ES.IP.RAT KILLER/TRAP   -> Pest Control

    # ---- Essential: Medicine (110xxx) ---------------------------------------
    "110000": 50,   # ES.MEDICINE             -> OTC Medicines
    "110001": 50,   # ES.OVER THE COUNTER MED -> OTC Medicines

    # ---- Essential: Wheat & Flour (120xxx) ----------------------------------
    "120000": 59,   # ES.WHEAT AND FLOUR      -> Rice, Noodles, Pasta
    "120001": 59,   # ES.WF.PASTA             -> Rice, Noodles, Pasta
    "120002": 22,   # ES.WF.BISCUITS/COOKIES  -> Cookies/Biscuits/Crackers
    "120003": 11,   # ES.WF.BREAD & PASTRIES  -> Bread
    "120004":  6,   # ES.WF.CAKES             -> Baking

    # ---- Non-Essential: Cigarettes/Wines/Spirits (201000) -------------------
    "201000": 64,   # NE.CIGARETTES/WINES     -> Tobacco (fallback)
    "201010": 64,   # NE.CW.CIGARETTES        -> Tobacco
    "201040": 66,   # NE.CW.LIQUOR            -> Wine (closest)
    "201050":  9,   # NE.CW.BEER              -> Beer
    "201090": 51,   # NE.CW.LIGHTER & FLUIDS  -> Other Home Items

    # ---- Non-Essential: Beverages (201100) ----------------------------------
    "201100": 62,   # NE.BEVERAGES            -> Soft Drinks (fallback)
    "201110": 62,   # NE.BV.SOFTDRINKS BOTTLED-> Soft Drinks
    "201111": 62,   # NE.BV.SOFTDRINKS CANNED -> Soft Drinks
    "201112": 44,   # NE.BV.JUICES BOTTLED    -> Juice & Iced Tea
    "201113": 44,   # NE.BV.JUICES CANNED     -> Juice & Iced Tea
    "201114": 44,   # NE.BV.JUICES TETRA      -> Juice & Iced Tea
    "201115": 65,   # NE.BV.WATER             -> Water
    "201116": 44,   # NE.BV.TEA               -> Juice & Iced Tea
    "201117": 44,   # NE.BV.JUICES POWDERED   -> Juice & Iced Tea
    "201118": 62,   # NE.BV.SOFTDRINKS TANK   -> Soft Drinks
    "201119": 62,   # NE.BV.SOFTDRINKS SYRUP  -> Soft Drinks
    "201120": 63,   # NE.BV.ENERGY DRINKS     -> Sports/Energy Drink

    # ---- Non-Essential: Personal Hygiene (201500) ---------------------------
    "201500": 10,   # NE.PERSONAL HYGIENE     -> Body Care (fallback)
    "201510": 49,   # NE.PH.ORAL CARE         -> Oral Care
    "201511": 10,   # NE.PH.BODY CARE         -> Body Care
    "201512": 38,   # NE.PH.HAIR CARE         -> Hair Care
    "201513": 61,   # NE.PH.FACIAL CARE       -> Skin Care & Cosmetics
    "201514": 61,   # NE.PH.NAIL CARE         -> Skin Care & Cosmetics
    "201515":  3,   # NE.PH.ALCOHOL           -> Alcohol & Sanitizers
    "201516": 10,   # NE.PH.DEODORANT         -> Body Care
    "201517": 10,   # NE.PH.BATH SOAP         -> Body Care
    "201518": 10,   # NE.PH.LOTION            -> Body Care
    "201519": 29,   # NE.PH.FEMININE WASH     -> Feminine Care
    "201520": 38,   # NE.PH.SHAMPOO&COND      -> Hair Care
    "201521": 10,   # NE.PH.COTTON/BUDS       -> Body Care

    # ---- Non-Essential: Ready-to-Wear (201300) ------------------------------
    "201300": 68,   # NE.READY TO WEAR        -> Ready-to-Wear
    "201310": 68,   # NE.RW.CHILDREN'S WEAR   -> Ready-to-Wear
    "201330": 68,   # NE.RW.MEN'S WEAR        -> Ready-to-Wear
    "201350": 68,   # NE.RW.LADIES WEAR       -> Ready-to-Wear
    "201370": 30,   # NE.RW.SHOES & SHOE CARE -> Foot Wear
    "201390": 30,   # NE.RW.SLIPPERS RUBBER   -> Foot Wear
    "201391": 30,   # NE.RW.SLIPPERS LEATHER  -> Foot Wear
    "201392": 69,   # NE.RW.UNDERWEAR         -> Underwear & Lingerie
    "201393": 70,   # NE.RW.ACCESSORIES       -> Accessories

    # ---- Non-Essential: Food Groups (202xxx) --------------------------------
    "202000": 14,   # NE.FOOD GROUPS          -> Canned Goods (fallback)
    "202016": 47,   # NE.FG.IMPORTED MILK     -> Milk
    "202019": 14,   # NE.FG.PRESERVED FRUITS  -> Canned Goods
    "202021": 60,   # NE.FG.SEASONING         -> Sauce, Spices, Spreads
    "202022": 13,   # NE.FG.NUTS              -> Candy & Chocolates
    "202023": 14,   # NE.FG.IMPRTD CANNED MEAT-> Canned Goods
    "202024": 14,   # NE.FG.IMPRTD BEANS      -> Canned Goods
    "202025": 14,   # NE.FG.IMPRTD VEGETABLES -> Canned Goods
    "202026": 14,   # NE.FG.IMPRTD FRUITS     -> Canned Goods
    "202027": 14,   # NE.FG.IMPRTD SARDINES   -> Canned Goods
    "202029": 21,   # NE.FG.COFFEE CREAMER    -> Coffee & Creamer

    # ---- Non-Essential: Baby Care (203xxx) ----------------------------------
    "203000":  5,   # NE.BABY CARE            -> Baby Skin Care (fallback)
    "203010":  5,   # NE.BC.BABY OIL          -> Baby Skin Care
    "203011":  5,   # NE.BC.BABY POWDER       -> Baby Skin Care
    "203012":  5,   # NE.BC.BABY COLOGNE      -> Baby Skin Care
    "203013":  5,   # NE.BC.BABY LOTION       -> Baby Skin Care
    "203014":  4,   # NE.BC.BABY SHAMPOO      -> Baby Bath Items
    "203015": 28,   # NE.BC.BABY FOOD         -> Feeding & Nursing
    "203050": 28,   # NE.BC.FEEDING BTLS      -> Feeding & Nursing
    "203060": 25,   # NE.BC.DIAPERS           -> Diapers
    "203080": 68,   # NE.BC.INFANT WEAR       -> Ready-to-Wear

    # ---- Non-Essential: Household (204xxx) ----------------------------------
    "204000": 51,   # NE.HOUSEHOLD            -> Other Home Items
    "204010": 45,   # NE.HH.KITCHEN PLSTIC    -> Kitchen & Dining
    "204011": 45,   # NE.HH.KITCHEN GLASS     -> Kitchen & Dining
    "204012": 45,   # NE.HH.KITCHEN CHINA     -> Kitchen & Dining
    "204013": 20,   # NE.HH.CLEANING AIDS     -> Cleaning Tools
    "204014": 51,   # NE.HH.BATH/FACE TOWEL   -> Other Home Items
    "204015": 40,   # NE.HH.WAX               -> Household Cleaners
    "204016": 40,   # NE.HH.AIR FRESHENERS    -> Household Cleaners
    "204017": 51,   # NE.HH.MATCHES           -> Other Home Items
    "204018": 51,   # NE.HH.TABLE NAPKINS/FOIL-> Other Home Items
    "204019": 51,   # NE.HH.PILLOWS           -> Other Home Items
    "204020": 51,   # NE.HH.MATS              -> Other Home Items
    "204021": 51,   # NE.HH.BLANKETS          -> Other Home Items
    "204022": 46,   # NE.HH.FABRIC CARE       -> Laundry Items
    "204023": 51,   # NE.HH.MOSQUITO NET      -> Other Home Items
    "204024": 45,   # NE.HH.KITCHEN WOOD      -> Kitchen & Dining
    "204025": 45,   # NE.HH.KITCHEN ALUM/STL  -> Kitchen & Dining
    "204026": 45,   # NE.HH.KITCHEN AID       -> Kitchen & Dining
    "204027": 51,   # NE.HH.CHARCOAL          -> Other Home Items
    "204028": 51,   # NE.HH.TOILETRIES.TISSUE -> Other Home Items
    "204029": 29,   # NE.HH.SAN.NAPKIN        -> Feminine Care

    # ---- Non-Essential: Office Supplies (205xxx) ----------------------------
    "205000": 53,   # NE.OFFICE SUPPLIES      -> Other Supplies
    "205010": 53,   # NE.OS.PUNCHER           -> Other Supplies
    "205011": 53,   # NE.OS.CALCULATOR        -> Other Supplies
    "205012": 53,   # NE.OS.PAPER CLIPS       -> Other Supplies
    "205013": 53,   # NE.OS.FASTENER          -> Other Supplies
    "205014": 54,   # NE.OS.BOND/BOOK PAPER   -> Paper & Notebook
    "205015": 53,   # NE.OS.CARBON PAPER      -> Other Supplies
    "205016": 53,   # NE.OS.PLASTIC ENVELOPES -> Other Supplies
    "205017": 53,   # NE.OS.SLIDING FOLDERS   -> Other Supplies
    "205019": 53,   # NE.OS.ENVELOPES         -> Other Supplies
    "205020": 53,   # NE.OS.TAPES             -> Other Supplies
    "205021": 53,   # NE.OS.BOOKENDS          -> Other Supplies
    "205022": 53,   # NE.OS.STAMP PAD/INK     -> Other Supplies
    "205023": 55,   # NE.OS.MARKERS           -> Pens & Markers
    "205024": 54,   # NE.OS.MAGAZINE          -> Paper & Notebook
    "205025": 54,   # NS.OS.POCKETBOOKS       -> Paper & Notebook

    # ---- Non-Essential: Personal Accessories (206xxx) -----------------------
    "206000": 70,   # NE.PERSONAL ACCESSORIES -> Accessories
    "206010": 27,   # NE.PA.EYEWEAR           -> Eyewear
    "206011": 70,   # NE.PA.JEWELRIES         -> Accessories
    "206012": 70,   # NE.PA.WALLET/PURSE      -> Accessories
    "206013": 70,   # NE.PA.BELT              -> Accessories

    # ---- Non-Essential: Miscellaneous (209xxx) ------------------------------
    "209000": 51,   # NE.MISC                 -> Other Home Items
    "209010": 52,   # NE.MS.TOYS              -> Other Kids Toys
    "209011": 37,   # NE.MS.GIFT ITEMS        -> Gift Items
    "209012":  6,   # NE.MS.BAKERY SUPPLIES   -> Baking
    "209013": 39,   # NE.MS.HARDWARE-SCREWS   -> Hardware Tools
    "209014": 26,   # NE.MS.ELECTRICAL SUPPLIES->Electrical
    "209015": 51,   # NE.MS.PLASTIC PRODUCTS  -> Other Home Items
    "209016": 51,   # NE.MS.CANDLES           -> Other Home Items
    "209017": 53,   # NE.MS.SEWING MATERIALS  -> Other Supplies
    "209018": 51,   # NE.MS.CAMERA            -> Other Home Items
    "209019": 51,   # NE.MS.HOME APPLIANCES   -> Other Home Items
    "209020": 57,   # NE.MS.PET FOOD & CARE   -> Pet Food
    "209021": 51,   # NE.MS.FILMS             -> Other Home Items
    "209022":  7,   # NE.MS.BATTERIES         -> Batteries
    "209023": 51,   # NE.MS.DEVELOPING MAT'LS -> Other Home Items
    "209024": 51,   # NE.MS.PHONE CARD        -> Other Home Items
    "209025": 53,   # NE.MS.LAMINATION        -> Other Supplies
    "209026": 51,   # NE.MS.VHS/CASSETTE TAPE -> Other Home Items
    "209027": 51,   # NE.MS.DISKETTE          -> Other Home Items
    "209028": 53,   # NE.MS.XEROX/MATERIAL    -> Other Supplies
    "209029": 51,   # NE.MS.FLASHLIGHT        -> Other Home Items
    "209030": 39,   # NE.MS.HARDWARE-HINGES   -> Hardware Tools
    "209031": 39,   # NE.MS.HARDWARE-BOLTS    -> Hardware Tools
    "209032": 39,   # NE.MS.HARDWARE-PADLOCK  -> Hardware Tools
    "209033": 39,   # NE.MS.HARDWARE-PLUMBING -> Hardware Tools
    "209034": 39,   # NE.MS.HARDWARE-TOOLS    -> Hardware Tools
    "209035": 39,   # NE.MS.HARDWARE-HOOKS    -> Hardware Tools
    "209036": 39,   # NE.MS.HARDWARE-CHAIN    -> Hardware Tools
    "209037": 51,   # NE.MS.SWEEPSTAKES       -> Other Home Items
    "209038": 51,   # NE.MS.CD/DVD            -> Other Home Items
    "209039": 51,   # NE.MS.INTERNET CARD     -> Other Home Items
}


# ---------------------------------------------------------------------------
# DBF parser
# ---------------------------------------------------------------------------

def parse_fpb(path):
    with open(path, "rb") as f:
        data = f.read()
    num_recs = struct.unpack_from("<I", data, 4)[0]
    hdr_size = struct.unpack_from("<H", data, 8)[0]
    rec_size = struct.unpack_from("<H", data, 10)[0]
    fields = []
    offset = 32
    while data[offset] != 0x0D and offset < hdr_size:
        name = data[offset:offset+11].split(b"\x00")[0].decode("ascii", errors="replace")
        ftype = chr(data[offset+11])
        flen  = data[offset+16]
        fields.append((name, ftype, flen))
        offset += 32
    cls_map = {}
    for i in range(num_recs):
        rec = data[hdr_size + i*rec_size : hdr_size + (i+1)*rec_size]
        if rec[0:1] == b"*":
            continue
        row = {}
        pos = 1
        for name, ftype, flen in fields:
            row[name] = rec[pos:pos+flen].decode("cp437", errors="replace").strip()
            pos += flen
        cls_map[row["CLRKEY"]] = row["CLDESC"]
    return cls_map


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def compute_quality(description, name, brand_id, category_id, size):
    issues = []
    if not (description or "").strip(): issues.append("NEEDS_DESCRIPTION")
    if not (name or "").strip():         issues.append("NEEDS_NAME")
    if not brand_id:                      issues.append("NEEDS_BRAND")
    if not category_id:                   issues.append("NEEDS_CATEGORY")
    if not (size or "").strip():          issues.append("NEEDS_SIZE")
    return ("COMPLETE", 0) if not issues else (issues[0], 1)


def run(dry_run):
    # Load CLRKEY descriptions for logging
    cls_map = parse_fpb(FPB_PATH)

    # Load MERKEY -> CLRKEY from CSV
    merkey_clrkey = {}
    with open(CSV_PATH, newline="", encoding="utf-8-sig", errors="replace") as f:
        for row in csv.DictReader(f):
            mk = (row.get("MERKEY") or "").strip()
            ck = (row.get("CLRKEY") or "").strip()
            if mk and ck:
                merkey_clrkey[mk] = ck

    print(f"CLRKEY map entries   : {len(cls_map)}")
    print(f"MERKEY->CLRKEY loaded: {len(merkey_clrkey):,}")
    print(f"CLRKEY->cat mappings : {len(CLRKEY_TO_CAT)}")
    print()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT merkey, description, name, brand_id, size
        FROM products
        WHERE active=1 AND (category_id IS NULL OR category_id=0)
    """)
    products = cur.fetchall()
    total = len(products)
    print(f"Uncategorized products: {total:,}")
    print()

    # Load category names for display
    cur.execute("SELECT id, name FROM categories")
    cat_names = {r["id"]: r["name"] for r in cur.fetchall()}

    stats = dict(updated=0, no_clrkey=0, no_mapping=0)
    unmapped = {}

    for p in products:
        mk = p["merkey"]
        ck = merkey_clrkey.get(mk, "")
        if not ck:
            stats["no_clrkey"] += 1
            continue

        cat_id = CLRKEY_TO_CAT.get(ck)
        if not cat_id:
            stats["no_mapping"] += 1
            unmapped[ck] = unmapped.get(ck, 0) + 1
            continue

        dq, ne = compute_quality(
            p["description"], p["name"], p["brand_id"], cat_id, p["size"]
        )

        if not dry_run:
            cur.execute("""
                UPDATE products
                SET category_id=?, data_quality=?, needs_enrichment=?,
                    enrichment_notes='Category assigned via CLRKEY lookup',
                    updated_at=CURRENT_TIMESTAMP
                WHERE merkey=?
            """, (cat_id, dq, ne, mk))
        stats["updated"] += 1

        if not dry_run and stats["updated"] % BATCH_COMMIT == 0:
            conn.commit()
            print(f"  [{stats['updated']:>6}/{total}] committed...", flush=True)

    if not dry_run:
        conn.commit()
    conn.close()

    print(f"Updated      : {stats['updated']:,}")
    print(f"No CLRKEY    : {stats['no_clrkey']:,}")
    print(f"No mapping   : {stats['no_mapping']:,}")

    if unmapped:
        print(f"\nUnmapped CLRKEYs (add to CLRKEY_TO_CAT if needed):")
        for ck, cnt in sorted(unmapped.items(), key=lambda x: -x[1])[:20]:
            desc = cls_map.get(ck, "?")
            print(f"  {ck}  {cnt:>5}  {desc}")

    if dry_run:
        print("\nDRY RUN — no changes written.")
    else:
        print("\nDONE.")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(f"\nStarted: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    run(args.dry_run)


if __name__ == "__main__":
    main()
