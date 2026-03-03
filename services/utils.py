"""
Utility functions and constants shared across the application.
"""
import numpy as np
from datetime import datetime
from zoneinfo import ZoneInfo

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def clean_nan_values(data):
    """Convert NaN/inf values to None and numpy scalars to native Python types for valid JSON serialization."""
    if isinstance(data, dict):
        return {k: clean_nan_values(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [clean_nan_values(item) for item in data]
    elif isinstance(data, np.bool_):
        return bool(data)
    elif isinstance(data, np.integer):
        return int(data)
    elif isinstance(data, np.floating):
        if np.isnan(data) or np.isinf(data):
            return None
        return float(data)
    elif isinstance(data, np.ndarray):
        return [clean_nan_values(x) for x in data.tolist()]
    elif isinstance(data, float):
        if np.isnan(data) or np.isinf(data):
            return None
        return data
    return data

# ============================================================================
# MARKET DATA & UTILITIES
# ============================================================================

MAJOR_INDICES = {
    '^GSPC': 'S&P 500',
    '^DJI': 'Dow Jones',
    '^IXIC': 'NASDAQ',
    '^RUT': 'Russell 2000',
    '^VIX': 'VIX'
}

SECTOR_ETFS = {
    'XLK': 'Technology',
    'XLF': 'Financials',
    'XLV': 'Healthcare',
    'XLE': 'Energy',
    'XLI': 'Industrials',
    'XLP': 'Consumer Staples',
    'XLY': 'Consumer Discretionary',
    'XLB': 'Materials',
    'XLRE': 'Real Estate',
    'XLC': 'Communication',
    'XLU': 'Utilities'
}

# Top stocks by sector (major holdings from each sector ETF)
SECTOR_STOCKS = {
    'XLK': ['AAPL', 'MSFT', 'NVDA', 'AVGO', 'ORCL', 'CRM', 'CSCO', 'ACN', 'ADBE', 'AMD', 'INTC', 'TXN', 'QCOM', 'IBM', 'AMAT'],
    'XLF': ['BRK-B', 'JPM', 'V', 'MA', 'BAC', 'WFC', 'GS', 'MS', 'SPGI', 'BLK', 'AXP', 'C', 'SCHW', 'PGR', 'MMC'],
    'XLV': ['LLY', 'UNH', 'JNJ', 'ABBV', 'MRK', 'TMO', 'ABT', 'PFE', 'AMGN', 'DHR', 'BMY', 'MDT', 'ISRG', 'CVS', 'GILD'],
    'XLE': ['XOM', 'CVX', 'COP', 'EOG', 'SLB', 'MPC', 'PSX', 'VLO', 'DINO', 'OXY', 'WMB', 'HAL', 'DVN', 'TRGP', 'KMI'],
    'XLI': ['GE', 'CAT', 'UNP', 'HON', 'RTX', 'BA', 'DE', 'UPS', 'LMT', 'MMM', 'GD', 'NOC', 'FDX', 'CSX', 'NSC'],
    'XLP': ['PG', 'COST', 'KO', 'PEP', 'WMT', 'PM', 'MDLZ', 'MO', 'CL', 'KMB', 'GIS', 'SYY', 'STZ', 'KHC', 'HSY'],
    'XLY': ['AMZN', 'TSLA', 'HD', 'MCD', 'NKE', 'LOW', 'BKNG', 'SBUX', 'TJX', 'CMG', 'MAR', 'GM', 'F', 'ORLY', 'ROST'],
    'XLB': ['LIN', 'SHW', 'APD', 'FCX', 'ECL', 'NEM', 'DOW', 'DD', 'NUE', 'VMC', 'MLM', 'CTVA', 'PPG', 'ALB', 'IFF'],
    'XLRE': ['PLD', 'AMT', 'EQIX', 'CCI', 'PSA', 'O', 'WELL', 'SPG', 'DLR', 'VICI', 'AVB', 'EQR', 'VTR', 'ARE', 'ESS'],
    'XLC': ['META', 'GOOGL', 'GOOG', 'NFLX', 'DIS', 'CMCSA', 'VZ', 'T', 'CHTR', 'TMUS', 'EA', 'TTWO', 'WBD', 'OMC', 'LYV'],
    'XLU': ['NEE', 'SO', 'DUK', 'CEG', 'SRE', 'AEP', 'D', 'PCG', 'EXC', 'XEL', 'ED', 'PEG', 'WEC', 'ES', 'AWK']
}

# US Stock Market Holidays for 2025-2027
US_MARKET_HOLIDAYS = {
    # 2025
    (2025, 1, 1): "New Year's Day",
    (2025, 1, 20): "Martin Luther King Jr. Day",
    (2025, 2, 17): "Presidents Day",
    (2025, 4, 18): "Good Friday",
    (2025, 5, 26): "Memorial Day",
    (2025, 6, 19): "Juneteenth",
    (2025, 7, 4): "Independence Day",
    (2025, 9, 1): "Labor Day",
    (2025, 11, 27): "Thanksgiving Day",
    (2025, 12, 25): "Christmas Day",
    # 2026
    (2026, 1, 1): "New Year's Day",
    (2026, 1, 19): "Martin Luther King Jr. Day",
    (2026, 2, 16): "Presidents Day",
    (2026, 4, 3): "Good Friday",
    (2026, 5, 25): "Memorial Day",
    (2026, 6, 19): "Juneteenth",
    (2026, 7, 3): "Independence Day (Observed)",
    (2026, 9, 7): "Labor Day",
    (2026, 11, 26): "Thanksgiving Day",
    (2026, 12, 25): "Christmas Day",
    # 2027
    (2027, 1, 1): "New Year's Day",
    (2027, 1, 18): "Martin Luther King Jr. Day",
    (2027, 2, 15): "Presidents Day",
    (2027, 3, 26): "Good Friday",
    (2027, 5, 31): "Memorial Day",
    (2027, 6, 18): "Juneteenth (Observed)",
    (2027, 7, 5): "Independence Day (Observed)",
    (2027, 9, 6): "Labor Day",
    (2027, 11, 25): "Thanksgiving Day",
    (2027, 12, 24): "Christmas Day (Observed)",
}
