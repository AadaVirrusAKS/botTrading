"""
Master stock universe shared across scanners.
Single source of truth for symbols used throughout scanner scripts.

Categories:
  - OPTIONS_ELIGIBLE: High volume, weekly options, tight bid-ask spreads.
    Suitable for 0-2 DTE options, intraday momentum, and swing options plays.
  - REGULAR_STOCKS: Lower-liquidity or no-weekly-options names.
    Suitable for swing/positional equity scans only.
  - MASTER_ETF_UNIVERSE: ETFs for both options and equity scanning.
"""

from typing import List


# ---------------------------------------------------------------------------
# OPTIONS-ELIGIBLE STOCKS
# High daily volume (>5M avg), weekly options, tight spreads.
# These are safe for intraday AND options scanners.
# ---------------------------------------------------------------------------
OPTIONS_ELIGIBLE_STOCKS = [
    # Mega-cap tech (weekly options, massive volume)
    'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'META', 'NVDA', 'TSLA', 'AMD', 'AVGO', 'NFLX',
    # Semis & cloud
    'INTC', 'MU', 'ORCL', 'CRM', 'ADBE', 'QCOM', 'MRVL', 'AMAT', 'LRCX',
    # Financials
    'JPM', 'BAC', 'WFC', 'C', 'GS', 'MS', 'SCHW', 'V', 'MA', 'AXP', 'PYPL',
    # Healthcare / pharma
    'UNH', 'JNJ', 'LLY', 'ABBV', 'MRK', 'PFE', 'BMY', 'MRNA', 'GILD', 'AMGN',
    # Energy
    'XOM', 'CVX', 'COP', 'SLB', 'OXY', 'MPC', 'VLO', 'HAL',
    # Consumer / retail
    'WMT', 'COST', 'HD', 'LOW', 'TGT', 'NKE', 'SBUX', 'MCD', 'DIS', 'CMCSA',
    # Industrials / defense
    'BA', 'CAT', 'GE', 'HON', 'RTX', 'LMT', 'DE',
    # High-beta / meme — great for 0-DTE momentum
    'COIN', 'MARA', 'RIOT', 'SOFI', 'PLTR', 'SNAP', 'ROKU', 'SHOP', 'UBER', 'ABNB',
    'GME', 'AMC', 'HOOD', 'SMCI', 'RIVN', 'LCID', 'NIO', 'DKNG', 'RBLX',
    # Biotech with active options
    'VRTX', 'REGN', 'BIIB', 'CRSP', 'MRNA', 'BNTX',
    # Telecom
    'T', 'VZ', 'TMUS',
    # Staples
    'PG', 'KO', 'PEP', 'PM', 'CL',
]

# ---------------------------------------------------------------------------
# REGULAR-ONLY STOCKS
# Lower volume / no weekly options — equity swing / positional scans only.
# Do NOT feed these into intraday or options scanners.
# ---------------------------------------------------------------------------
REGULAR_ONLY_STOCKS = [
    # Software / SaaS (lower volume)
    'NOW', 'SNOW', 'CRWD', 'NET', 'DDOG', 'ZS', 'FTNT', 'PANW', 'MDB', 'OKTA',
    'DOCN', 'CFLT', 'S', 'BILL', 'FROG', 'MNDY', 'PATH', 'FOUR', 'ESTC', 'DOMO',
    'DBX', 'BOX', 'TTD', 'VEEV', 'TEAM', 'ZM', 'DOCU', 'SPOT', 'PINS', 'RDDT',
    # EV / clean energy
    'HYLN', 'QS', 'CHPT', 'BLNK', 'BE', 'ENPH', 'SEDG', 'RUN', 'WOLF', 'AMPX',
    'EVGO', 'CLSK', 'XPEV', 'LI',
    # Biotech / pharma (lower volume)
    'VXRT', 'OCGN', 'INO', 'ARWR', 'EDIT', 'NTLA', 'BEAM', 'RARE', 'FOLD', 'IONS',
    'ILMN', 'ALNY', 'BMRN', 'INCY', 'EXAS', 'ARGX', 'NBIX', 'JAZZ', 'UTHR', 'HALO',
    'AVXL', 'TNXP', 'IMMP', 'MVIS', 'CODX', 'RVPH', 'AKTX',
    # Retail / consumer (lower volume)
    'CHWY', 'W', 'RH', 'BBWI', 'ANF', 'AEO', 'CATO', 'PLCE', 'DDS', 'PRTS',
    'M', 'KSS', 'TJX', 'ROST', 'BURL', 'FIVE', 'DG', 'DLTR', 'BBY', 'URBN',
    'LULU', 'ETSY', 'EBAY',
    # International e-commerce
    'MELI', 'SE', 'CPNG', 'BABA', 'JD', 'PDD',
    # Fintech / specialty finance
    'UPST', 'AFRM', 'LC', 'OPFI', 'BLK', 'TROW', 'BX', 'KKR', 'ALLY', 'NU', 'OPEN', 'MSTR',
    # Energy (lower volume)
    'EOG', 'PSX', 'BKR', 'DVN', 'FANG', 'TRGP', 'APA', 'CTRA', 'OVV', 'PR', 'SM',
    # Real estate / auto / misc
    'COMP', 'EXPI', 'Z', 'RMAX', 'RKT', 'UWMC', 'CVNA', 'CARG', 'KMX', 'AN',
    'LAD', 'SAH', 'ABG', 'PAG', 'GPI', 'AAP', 'AZO', 'ORLY', 'TSCO', 'DKS',
    # Insurance
    'XOS', 'ROOT', 'LMND', 'AFG', 'KNSL', 'MCY', 'PGR', 'TRV', 'ALL', 'AIG',
    # Aerospace / defense (lower vol)
    'RKLB', 'RDW', 'NOC', 'GD', 'LHX', 'HWM',
    # Media
    'CHTR', 'LYV', 'WBD', 'FOX',
    # Semis (lower vol)
    'TSM', 'ADI', 'KLAC', 'NXPI', 'MCHP', 'ON', 'SWKS', 'QRVO', 'MPWR', 'CRUS', 'TXN',
    # Cannabis
    'MSOS', 'VFF', 'CURLF', 'TCNNF',
    # Industrials (lower vol)
    'ETN', 'EMR', 'ITW', 'MMM', 'PH', 'CMI',
    # Consumer staples (lower vol)
    'MO', 'KHC', 'MDLZ', 'BYND',
    # Speculative / small-cap
    'IREN', 'RCAT', 'PHUN', 'BKKT', 'APLD', 'IONQ', 'AEHR', 'RXRX', 'VET',
    'BB', 'KOSS', 'DJT', 'GLBE', 'HIMS', 'JOBY', 'ASTS', 'GRWG', 'IIPR',
    # Large-cap (lower options vol)
    'BRK-B', 'TMO', 'ABT', 'DHR', 'CVS', 'CI', 'HUM', 'BSX',
    'BKNG', 'CMG', 'YUM', 'LIN', 'APD', 'ECL', 'NEM', 'FCX', 'NUE',
    'NEE', 'DUK', 'SO', 'D', 'AMT', 'PLD', 'CCI', 'EQIX', 'SPG',
    'ASML', 'NVO', 'UNP', 'UPS', 'GOOG', 'DASH', 'LYFT', 'U',
]

# Combined master universe (backwards compatible)
MASTER_STOCK_UNIVERSE = OPTIONS_ELIGIBLE_STOCKS + REGULAR_ONLY_STOCKS

MASTER_ETF_UNIVERSE = [
    'SPY', 'QQQ', 'IWM', 'DIA', 'VOO', 'VTI', 'EEM', 'GLD', 'SLV', 'TLT',
    'HYG', 'XLF', 'XLE', 'XLK', 'XLV', 'XLI', 'XLP', 'XLY', 'XLU', 'XLRE',
    'SMH', 'XBI', 'IBB', 'XRT', 'XME', 'XOP', 'ARKK', 'ARKG', 'ARKF', 'ARKQ',
    'ARKW', 'TAN', 'ICLN', 'LIT', 'REMX', 'URA', 'GDX', 'GDXJ', 'SIL', 'USO',
    'UNG', 'FXI', 'EWZ', 'EWJ', 'INDA', 'MCHI', 'KWEB', 'YINN', 'TQQQ', 'SQQQ',
    'SPXL', 'SPXS', 'UPRO', 'TMF', 'TZA', 'UVXY', 'VIXY', 'TECL', 'SOXL', 'NAIL'
]

# ETFs with active weekly options (for options scanner)
OPTIONS_ELIGIBLE_ETFS = [
    'SPY', 'QQQ', 'IWM', 'DIA', 'GLD', 'SLV', 'TLT', 'XLF', 'XLE', 'XLK',
    'XLV', 'XLI', 'XLY', 'SMH', 'XBI', 'XOP', 'TQQQ', 'SQQQ', 'SOXL', 'UVXY',
]


def get_master_stock_list(include_etfs: bool = True) -> List[str]:
    """Return deduplicated master symbol list for scanners."""
    symbols = MASTER_STOCK_UNIVERSE + (MASTER_ETF_UNIVERSE if include_etfs else [])
    return sorted(set(symbols))


def get_options_eligible(include_etfs: bool = True) -> List[str]:
    """Return only options-eligible symbols (high volume, weekly options).
    Use this for intraday and options scanners to avoid low-liquidity names.
    """
    symbols = list(OPTIONS_ELIGIBLE_STOCKS)
    if include_etfs:
        symbols += OPTIONS_ELIGIBLE_ETFS
    return list(dict.fromkeys(symbols))  # dedupe preserving order


def get_regular_stocks() -> List[str]:
    """Return regular (non-options-eligible) stocks for swing/positional scans."""
    return list(REGULAR_ONLY_STOCKS)


def merge_unique_symbols(*symbol_lists: List[str]) -> List[str]:
    """Merge multiple symbol lists preserving order and removing duplicates."""
    merged = []
    seen = set()
    for symbols in symbol_lists:
        for symbol in symbols:
            cleaned = str(symbol).strip().upper()
            if not cleaned or cleaned in seen:
                continue
            merged.append(cleaned)
            seen.add(cleaned)
    return merged


def get_master_stock_list_with_additions(additional_symbols: List[str], include_etfs: bool = True) -> List[str]:
    """Return master universe merged with additional symbols, deduplicated."""
    base = MASTER_STOCK_UNIVERSE + (MASTER_ETF_UNIVERSE if include_etfs else [])
    return merge_unique_symbols(base, additional_symbols)


def get_intraday_core_list() -> List[str]:
    """Smaller liquid list for intraday-style scanners (subset of options-eligible)."""
    core = [
        'SPY', 'QQQ', 'IWM', 'DIA', 'AAPL', 'MSFT', 'NVDA', 'TSLA', 'AMD',
        'AMZN', 'META', 'GOOGL', 'NFLX', 'JPM', 'BAC', 'XLF', 'XLK', 'XLE',
        'XOM', 'CVX', 'UNH', 'WMT', 'COST'
    ]
    return merge_unique_symbols(core)
