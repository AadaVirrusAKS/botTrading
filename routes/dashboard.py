"""
Dashboard Routes - Market overview, sectors, movers, dashboard batch API.
"""
from flask import Blueprint, render_template, jsonify, request
import json
import os
import time
import threading
from datetime import datetime
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed
import yfinance as yf
import numpy as np
import pandas as pd

from services.utils import MAJOR_INDICES, SECTOR_ETFS, SECTOR_STOCKS, clean_nan_values
from services.symbols import filter_valid_symbols
from services.market_data import (
    cached_batch_prices, cached_get_price, cached_get_history,
    cached_get_ticker_info, _is_rate_limited, _log_fetch_event,
    _price_cache, _price_cache_lock, _is_rate_limit_error, _mark_rate_limited,
    _mark_global_rate_limit, _is_expected_no_data_error, _is_globally_rate_limited,
    quote_cache, cache_timeout, fetch_quote_api_batch, _fetch_all_quotes_batch
)
from services.market_helpers import (
    get_market_status, get_live_quote, get_sector_performance,
    get_top_movers, get_premarket_movers, get_afterhours_movers,
    get_extended_hours_data
)

dashboard_bp = Blueprint("dashboard", __name__)

# ============================================================================
# DISK CACHE - Persist dashboard caches across server restarts
# ============================================================================
_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.dashboard_cache')
_BATCH_CACHE_FILE = os.path.join(_CACHE_DIR, 'batch_cache.json')
_BREADTH_CACHE_FILE = os.path.join(_CACHE_DIR, 'breadth_cache.json')

def _save_cache_to_disk(filepath, data, timestamp):
    """Persist a cache dict + timestamp to disk as JSON."""
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        payload = {'data': data, 'timestamp': timestamp.isoformat()}
        with open(filepath, 'w') as f:
            json.dump(payload, f, default=str)
    except Exception as e:
        print(f"Cache save error ({filepath}): {e}")

def _load_cache_from_disk(filepath, max_age_seconds):
    """Load cache from disk if it exists and hasn't expired."""
    try:
        if not os.path.exists(filepath):
            return None, None
        with open(filepath) as f:
            payload = json.load(f)
        ts = datetime.fromisoformat(payload['timestamp'])
        age = (datetime.now() - ts).total_seconds()
        if age > max_age_seconds:
            return None, None
        return payload['data'], ts
    except Exception as e:
        print(f"Cache load error ({filepath}): {e}")
        return None, None

# ============================================================================
# API ENDPOINTS
# ============================================================================

# ============================================================================
# UNIFIED BATCH API - Reduces multiple API calls to single request
# ============================================================================

# ============================================================================
# COMPREHENSIVE MARKET BREADTH (1800+ US Stocks)
# ============================================================================

_market_breadth_cache = {
    'data': None,
    'timestamp': None,
    'cache_ttl': 900,       # Market breadth data cached for 15 min (1600+ symbols, expensive)
    'refreshing': False,    # True while a background refresh is in progress
}
_market_breadth_lock = threading.Lock()

# Restore breadth cache from disk on import (accept up to 1 hour old for quick startup)
_disk_breadth, _disk_breadth_ts = _load_cache_from_disk(_BREADTH_CACHE_FILE, 3600)
if _disk_breadth is not None:
    _market_breadth_cache['data'] = _disk_breadth
    _market_breadth_cache['timestamp'] = _disk_breadth_ts
    print(f"📊 Restored market breadth cache from disk (age: {(datetime.now() - _disk_breadth_ts).total_seconds():.0f}s)")

# S&P 500 constituents (as of early 2026) - Yahoo Finance format (dots → dashes)
_SP500 = [
    'AAPL','ABBV','ABT','ACN','ADBE','ADI','ADM','ADP','ADSK','AEE','AEP','AES',
    'AFL','AIG','AIZ','AJG','AKAM','ALB','ALGN','ALL','ALLE','AMAT','AMCR','AMD',
    'AME','AMGN','AMP','AMT','AMZN','ANET','AON','AOS','APA','APD','APH',
    'APTV','ARE','ATO','AVB','AVGO','AVY','AWK','AXP','AZO',
    'BA','BAC','BAX','BBWI','BBY','BDX','BEN','BF-B','BG','BIIB','BIO','BK',
    'BKNG','BKR','BLK','BMY','BR','BRK-B','BRO','BSX','BWA','BX','BXP',
    'C','CAG','CAH','CARR','CAT','CB','CBOE','CBRE','CCI','CCL','CDNS',
    'CDW','CE','CEG','CF','CFG','CHD','CHRW','CHTR','CI','CINF','CL','CLX',    'CMCSA','CME','CMG','CMI','CMS','CNC','CNP','COF','COO','COP','COR','COST',
    'CPAY','CPB','CPRT','CPT','CRL','CRM','CSCO','CSGP','CSX','CTAS','CTRA',
    'CTSH','CTVA','CVS','CVX',
    'D','DAL','DAY','DD','DE','DECK','DG','DGX','DHI','DHR','DIS','DLTR',
    'DOV','DOW','DPZ','DRI','DTE','DUK','DVA','DVN',
    'DXCM','EA','EBAY','ECL','ED','EFX','EIX','EL','EMN','EMR','ENPH','EOG',
    'EPAM','EQIX','EQR','EQT','ERIE','ES','ESS','ETN','ETR','EVRG','EW','EXC',
    'EXPD','EXPE','EXR',
    'F','FANG','FAST','FBIN','FCX','FDS','FDX','FE','FFIV','FICO','FIS',
    'FISV','FITB','FMC','FOX','FOXA','FRT','FSLR','FTNT','FTV',
    'GD','GDDY','GE','GEHC','GEN','GILD','GIS','GL','GLW','GM','GNRC','GOOG',
    'GOOGL','GPC','GPN','GRMN','GS','GWW',
    'HAL','HAS','HBAN','HCA','HD','HOLX','HON','HPE','HPQ','HRL','HSIC','HST',
    'HSY','HUBB','HUM','HWM','IBM','ICE','IDXX','IEX','IFF','ILMN','INCY','INTC',
    'INTU','INVH','IP','IQV','IR','IRM','ISRG','IT','ITW','IVZ',
    'J','JBHT','JBL','JCI','JKHY','JNJ','JPM',
    'K','KDP','KEY','KEYS','KHC','KIM','KLAC','KMB','KMI','KMX','KO','KR',
    'KVUE','L','LDOS','LEN','LH','LHX','LIN','LKQ','LLY','LMT','LNT','LOW',
    'LRCX','LULU','LUV','LVS','LW','LYB','LYV',
    'MA','MAA','MAR','MAS','MCD','MCHP','MCK','MCO','MDLZ','MDT','MET','META',
    'MGM','MHK','MKC','MKTX','MLM','MMC','MMM','MNST','MO','MOH','MOS','MPC',
    'MPWR','MRK','MRNA','MRVL','MS','MSCI','MSFT','MSI','MTB','MTCH','MTD','MU',
    'NCLH','NDAQ','NDSN','NEE','NEM','NFLX','NI','NKE','NOC','NOW','NRG',
    'NSC','NTAP','NTRS','NUE','NVDA','NVR','NWS','NWSA','NXPI',
    'O','ODFL','OKE','OMC','ON','ORCL','ORLY','OTIS','OXY',
    'PANW','LYV','PAYC','PAYX','PCAR','PCG','PEG','PEP',
    'PFE','PFG','PG','PGR','PH','PHM','PKG','PLD','PM','PNC','PNR','PNW','POOL',
    'PPG','PPL','PRU','PSA','PSX','PTC','PVH','PWR','DINO',
    'PYPL','QCOM','QRVO','RCL','REG','REGN','RF','RHI','RJF','RL','RMD','ROK',
    'ROL','ROP','ROST','RSG','RTX','RVTY',
    'SBAC','SBUX','SCHW','SEE','SHW','SJM','SLB','SMCI','TRGP',
    'SNA','SNPS','SO','SOLV','SPG','SPGI','SRE','STE','STT','STX','STZ',
    'SWK','SWKS','SYF','SYK','SYY',
    'T','TAP','TDG','TDY','TECH','TEL','TER','TFC','TFX','TGT','THC','TJX','TMO',
    'TMUS','TPR','TRGP','TRMB','TROW','TRV','TSCO','TSLA','TSN','TT','TTWO','TXN',
    'TXT','TYL',
    'UAL','UDR','UHS','ULTA','UNH','UNP','UPS','URI','USB',
    'V','VICI','VLO','VLTO','VMC','VRSK','VRSN','VRTX','VST','VTR','VTRS','VZ',
    'WAB','WAT','WBD','WDC','WEC','WELL','WFC','WHR','WM','WMB','WMT',
    'WRB','WST','WTW','WY','WYNN',
    'XEL','XOM','XRAY','XYL','YUM','ZBH','ZBRA','ZION','ZTS'
]

# S&P MidCap 400 representative constituents
_SP400 = [
    'ACGL','ACM','ACIW','AEIS','AFG','AGCO','AIT','ALKS','AMKR','AOS',
    'ASGN','ASGN','ATR','AXON','AYI',    'BC','BJ','BRKR','BWXT','BYD',
    'CACI','CALM','CARG','CASY','CBSH','CC','CHDN','CHE','CIEN','CLH','COLM',
    'CRI','CRUS','CUZ','CW','CZR',
    'DCI','DINO','DOCS','DOCU','DT','DUOL','DXC',
    'EGP','EHC','ENSG','EPRT','ESI','ETSY','EVR','EWBC','EXEL','EXP',
    'FAF','FIVE','FHN','FIX','FN','FNF','FRPT','FLS','FROG',
    'G','GATX','GGG','GLOB','GNRC','GNTX','GTES','GWRE',
    'HAE','HAYW','HE','HESM','HGV','HLI','HLNE','HQY','HRB',
    'IART','IBP','ICFI','ICLR','IESC','IEX','INGR','IOSP',    'ITT','JAZZ','JBGS','JBSS','JEF','JHG','JLL',    'KBR','KEX','KMPR','KNSL','KNX','KNTK','KRG',
    'LAUR','LEA','LII','LITE','LIVN','LNTH','LSTR','LW','LXP',
    'MANH','MAN','MASI','MDGL','MEDP','MIDD','MKSI','MKTX','MLI','MOD',
    'MTH','MTSI','MUR','MUSA',
    'NBIX','NCNO','NEU','NOVT','NVT','NXST','NYT',
    'OC','OGE','OGN','OGS','OLED','OLN','ONEW','ORA','OSK','OTTR',    'PAYC','PB','PBF','PCOR','PEN','PII','PLNT','PNFP','POR',
    'POST','POWL','PPG','PRIM','PRGS','PRI','PTCT','PVH',
    'QLYS','QRVO','RBC','RBRK','RDN','REXR','RGLD','RH','RHP','RIG','RLI',
    'RMBS','RNR','ROIV','RPM','RS','RXO',
    'SAM','SAIC','SAIA','SBRA','SCI','SEIC','SF','SITE','SLM','SM',
    'SMPL','SON','SSD','STAG','STRA','SUM','SXT',
    'TDC','TECH','TENB','TFSL','TGNA','TKO','TKR','TMHC','TOL','TPG',    'TTMI','TW','TXRH','TYL',
    'UBSI','UFPI','UMBF','UNM','UPBD','URBN',
    'VCYT','VEEV','VFC','VIRT','VRNS','VRRM','VVV',
    'WAL','WDAY','WEX','WH','WHD','WING','WK','WOLF','WPC','WPM','WTM','WTRG',
    'X','XNCR','XPO','XRAY',
    'YEXT','ZWS'
]

# S&P SmallCap 600 representative constituents
_SP600 = [
    'ABCB','ABG','ABM','ACAD','ACCO','ACLS','ADNT','AEHL','AEHR',
    'AEO','AGEN','AGYS','AIN','AKR','ALGT','ALLO','ALRM',
    'AMBA','AMKR','AMPH','ANF','ANGO','ANIK','AORT','AM',
    'APPF','APPS','ARCB','ARDX','AROC','ARRY','ASB','ASIX','ASTE',
    'ATEC','ATGE','ATNI','ATRO','AVAV','AVO','AVNT','AX',
    'BALY','BDC','BEAT','BFAM','BGS','BHE','BJRI','BKE','BKH','BL','BLKB',
    'BMBL','BRC','BRZE','BTU','BWA','BXMT',
    'CAKE','CALX','CAR','CARG','CARS','CATY','CBT','CCS','CCSI','CDNA',
    'CENX','CEVA','CHH','CHWY','CIVB','CLDX','CLF',    'CMP','CMPR','CNK','CNMD','CNO','CNS','CNXN','COHU','CORT',
    'CPRX','CPK','CRAI','CROX','CSL','CSTL','CSTM','CTBI','CTLP',
    'CTO','CVBF','CVCO','CVI','CVLT','CWST','CWT','CXW',
    'DDS','DIOD','DLB','DLX','DNLI','DORM','DRH','DSP',
    'ECPG','EGO','EIG','ENR','ENSG','ENV','EPR','ESNT',    'EVTC','EXLS','EXPI','EXPO','EZPW',
    'FBP','FCFS','FCNCA','FDP','FELE','FHB','FHI','FIZZ','FNB',
    'FOLD','FORM','FOXF','FRGE','FRME','FWRD','GBX',
    'GIII','GNTX','GOLF','GPI','GPOR','GRWG','GSHD','GTY','GWRS',
    'HAIN','HAYW','HCAT','HCSG','HELE','HESM','HFWA','HLF','HMN',
    'HNI','HUBG','HURN','HWC','HWKN',
    'IAC','IBKR','IBOC','ICFI','ICHR','IDCC','INDB','INSM','INSW',
    'IOSP','IPAR','IRT','IRWD',    'JACK','JBGS','JBLU','JJSF','JKHY','JOE','JRVR',
    'KALU','KFRC','KMT','KN','KNSA','KREF','KWR',
    'LBRT','LCII','LDI','LFUS','LGIH','LIVN','LMAT','LMNR','LGND',
    'LOPE','LPLA','LPX','LQDT','LSCC','LUNA','LXP',
    'MATX','MBC','MCRI','MD','MGEE','MGPI','MGRC','MLAB',
    'MLKN','MMSI','MNKD','MOD','MORN','MRCY','MSGE','MSGS','MTDR','MTLS',
    'MTRN','MVRL','MWA','MXL','MYE',
    'NABL','NAVI','NBR','NBTB','NEOG','NHC','NINE','NOG','NOVT',    'NSA','NSP','NSSC','NTB','NTCT','NTST','NWE',    'OFG','OII','OLPX','OMI','ONB','ONTO','OOMA','OPK','OSIS',
    'OTTR','OXM','PATK','PBH','PLXS','PRAA','POWL','PRA',
    'PRLB','PRPL','PTGX','PUMP','PVH',
    'QTWO','R','RCKT','RCKY','RDNT','REPL','REZI','RGR','RIG',
    'RLGT','RMBS','RNG','RNST','ROCK','ROG','RPD','RRBI','RRR','RUN',
    'SABR','SAFE','SAH','SAIL','SANM','SBH','SBRA','SCHL','SEM','SGHT',
    'SHC','SITM','SKWD','SKY','SLGN','SLM','SMBC','SMPL','SNBR','SNEX',
    'SPNT','SPSC','SR','SRI','STAA','STEP','SWIM','SWX',
    'TCBI','TDC','TERN','TGTX','TILE','TNC','TNDM','TRNO','TRNR','TRUP','TTI',
    'TVTX','TXRH','TYL',
    'UFPI','UMBF','UNIT','UPWK','USPH','UTL',
    'VAC','VCYT','VECO','VERX','VG','VIR','VIRC','VIRT',    'VRNS','VRRM','VSEC','VVV',
    'WABC','WAFD','WBS','WD','WK','WOR','WRBY','WSBF','WSC',
    'XNCR','XRX',
    'YEXT','YORW',
    'ZION','ZWS'
]

# Additional popular stocks not in S&P indices (meme, EV, crypto, cannabis, etc.)
_ADDITIONAL = [
    'RDDT','PLTR','HOOD','SOFI','COIN','MSTR','RBLX','SNAP','PINS','ROKU',
    'DKNG','DASH','U','SHOP','TTD','DDOG','NET','CRWD','ZS','MDB','SNOW',
    'OKTA','MNDY','BILL','ESTC','CFLT','DOCN','FROG',
    'RIVN','LCID','NIO','XPEV','LI','JOBY','CHPT','BLNK',
    'QS','BE','BNTX','CRSP','NTLA','BEAM','EDIT','ARWR',
    'IONS','RARE','AVXL',
    'GME','AMC','BB','DJT','ASTS','IONQ',
    'MARA','RIOT','CLSK','HUT','BITF','IREN','APLD',
    'AFRM','UPST','HIMS','RXRX','RCAT','LUNR','SOUN','BBAI','AI','GLBE','PATH',
    'CELH','DUOL','CAVA','BIRK',
    'SPY','QQQ','IWM','DIA','VTI','VOO',
]

# Extra US-listed stocks to fill out the universe to 1500+
_EXTRA_US = [
    # Russell 2000 / broader market names not already in S&P lists
    'ABNB','ACHR','ADMA','AGTI','AHR','ALSN','AMRX','ANSS','APP','ARCC',
    'ARES','ARMK','ASND','ATMU','AZEK',
    'BALL','BECN','BFST','BLDR','BMA','BMR','BPMC','BRBR','BSM','BURL',
    'CADE','CACC','CAMT','CBRL','CCOI','CDAY','CEIX','CGNX','CHEF','CIEN',
    'CIFR','CLS','CLVT','CMBM','CNNE','CNXC','COHR','COMP','COUR','CPNG',
    'CPRX','CRGY','CRNX','CRSP','CSWI','CYBR',
    'DAR','DBX','DBRG','DCOM','DCPH','DFH','DIOD','DMRC','DNA','DNB',
    'DOCS','DOLE','DORM','DSGX','DV','DVAX',
    'EAT','EEFT','EFSC','ELAN','ELF','ENFN','ENOV','ENVX','ESGR','ESRT',
    'ESTA','ESTC','EVCM','EVGO','EXAI','EXLS',
    'FBK','FCEL','FCPT','FG','FIGS','FLNC','FLUT','FLYW','FOUR','FRBK',
    'FRSH','FRVT','FTAI','FTRE','FUBO',
    'GATO','GBCI','GCMG','GCT','GEO','GFL','GKOS','GMS','GO','GOOS',
    'GRAB','GRFS','GRIN','GTLS','GTLB','GYRE',
    'HBI','HEES','HELE','HGV','HHH','HII','HLLY','HLVX','HMC','HNST',
    'HOUS','HRMY','HUBG','HUBS',
    'IBN','ICL','IESC','IIPR','INFA','INSP','INST','INTA','IRTC','ITCI',
    'JAMF','JANX','JBSS','JNPR','JOBY',
    'KARO','KD','KLIC','KOS','KTOS','KVYO',
    'LANC','LBRDK','LESL','LFST','LGF-A','LHCG','LICY','LMND','LNW','LPRO',
    'LSCC','LSPD','LTCH','LUMN','LYEL','LYFT',
    'MANU','MAPS','MATV','MBLY','MC','MCFT','MCW','MDXG','ME','MGNI',
    'MIRM','MKTW','MLCO','MMYT','MODV','MOMO','MPLN','MREO','MRTN','MRUS',
    'MSTR','MTCH','MTDR','MTTR','MVST',
    'NAT','NBHC','NCMI','NERV','NETI','NEXT','NGVT','NNOX','NOVA',
    'NTRA','NUVL','NWL','NXRT','NYCB',
    'OFIX','OLK','OLPX','OPEN','OPCH','ORA','ORIC','OWL',
    'PARR','PAX','PAYC','PCTY','PCVX','PDCO','PDFS','PEGA','Penn','PGNY',
    'PINC','PLAY','PLMR','PLUS','PLTK','PLYA','PNFP','PRCT','PRFT','PRGS',
    'PRKS','PRMW','PROF','PRPL','PSFE','PUBM','PYCR',
    'QTWO','QUBT','RAMP','RCEL','RCUS','RDW','REAL','REAX','RELY','RENT',
    'RERE','REVG','RIOT','RIVN','RKLY','RMBS','RNG','RNGR','ROIC','ROOT',
    'RPM','RPAY','RPRX','RRR','RRX','RSKD','RVMD','RVNC','RXO','RYAM',
    'SAGE','SAIA','SANA','SAR','SB','SCCO','SDGR','SEMR','SES','SHAK',
    'SHLS','SIGA','SIG','SIRI','SIX','SKIL','SKX','SLNO','SLB','SMMT',
    'SNCR','SNDR','SOLO','SONO','SQSP','SRAD','SRC','SRPT','SSRM','STNE',
    'SUI','SUPN','SWTX','SYMX',
    'TDW','TECK','TENB','TEN','TFII','TGTX','THS','TKC','TMCI',
    'TMHC','TOI','TOST','TPL','TRIP','TRUE','TRUP','TTEK','TTGT','TWLO',
    'TWST','TXG',
    'UAA','UDMY','URBN','USFD',
    'VCEL','VFC','VIR','VITL','VLDR','VNDA','VNET','VNO','VNOM','VTEX',
    'VYNT','W','WDFC','WEN','WFRD','WGS','WHD','WISH','WKHS',
    'WOLF','WOR','WSM','WULF',
    'XENE','XM','XP','XPRO','YELP','YOU','YQ','YSG',
    'ZD','ZEN','ZENV','ZG','ZLAB','ZM','ZUO',
    # More Russell 2000 / Nasdaq-listed fillers to reach 1500+
    'AAON','ABCL','ACAD','ACHC','ACRE','ADPT','ADUS','AIRS','AKA','AKRO',
    'ALEC','ALNY','ALRM','AMPH','ANIK','AORT','APLT','ARLP','ARQT','ARVN',
    'ATAT','AUPH','AVDX','AVPT','AXSM','AZTA',
    'BAND','BBIO','BCOV','BDTX','BGCP','BGNE','BHG','BKSY','BLI','BMEA',
    'BNGO','BONE','BOWL','BRCC','BTU','BTAI','BYSI',
    'CARG','CARS','CCRN','CCSI','CLBT','CLNE','CLPT','CMPO','CNSP','COLB',
    'COOP','CORT','COUR','CPSI','CRVL','CTMX','CTOS','CUBI','CVGW',
    'DAWN','DAVA','DCGO','DH','DIN','DXPE','DYNE',
    'EGHT','ELAN','ELME','ENTA','EOLS','ERJ','EVLV','EXTR',
    'FATE','FCFS','FDMT','FLGT','FLWS','FMBI','FORR','FOXF','FROG',
    'GBTG','GCO','GEVO','GFAI','GH','GIII','GLNG','GPP','GVA',
    'HAFC','HCC','HLX','HMST','HOWL','HTLD','HYLN',
    'ICAD','IMCR','IMVT','INBX','INDI','INMD','IOVA','IOVA',
    'JANX','JWN','KALA','KRUS','KURA',
    'LAZR','LEGN','LIVN','LNTH','LPRO','LRN','LXRX',
    'MAXN','MCBS','MDB','MEDP','MGRC','MIGI','MLNK','MQ','MTSI',
    'NAAS','NBIX','NCNO','NKTR','NKLA','NMRA','NPAB','NR','NTNX','NUS',
    'OABI','OBNK','OCUL','OLLI','OMGA','OPRX','OPT','OSIS',
    'PAYO','PCOR','PD','PHAT','PIPR','PLAB','PLRX','PNT','PPC',
    'PRTA','PTON','PZZA',
    'QLYS','QSR','RBBN','RCMT','RDFN','REPX','RGNX','RIOT',
    'SATS','SCPL','SCSC','SDRL','SFBS','SGFY','SHO','SLAB','SLVM',
    'SMTC','SNCY','SPOK','SPWR','SPXC','SSYS','STRL','SWBI',
    'TBI','TBBK','TCBK','TILE','TMDX','TMST','TNXP','TPC','TPTX','TREX',
    'TRMK','TVTX','UCBI','UFPT','UGI','UNFI','UVE','UVV',
    'VBTX','VCNX','VERV','VGSH','VHI','VIRT','VSCO','VTS',
    'WAFD','WARM','WGO','WINA','WSR','WTFC',
    'XPER','YELP','YEXT',
]


def _get_broad_market_tickers():
    """Get comprehensive US market ticker list (1500+ stocks) - hardcoded for reliability"""
    dashboard_symbols = _SP500 + _SP400 + _SP600 + _ADDITIONAL + _EXTRA_US
    # Deduplicate while preserving order
    seen = set()
    merged_symbols = []
    for s in dashboard_symbols:
        su = s.strip().upper()
        if su and su not in seen:
            seen.add(su)
            merged_symbols.append(su)
    return filter_valid_symbols(merged_symbols)


def calculate_market_breadth():
    """Calculate market breadth for 1500+ US stocks using yf.download()"""
    now = datetime.now()

    # Return cached data if valid
    with _market_breadth_lock:
        if (_market_breadth_cache['data'] is not None and
            _market_breadth_cache['timestamp'] is not None):
            age = (now - _market_breadth_cache['timestamp']).total_seconds()
            if age < _market_breadth_cache['cache_ttl']:
                return _market_breadth_cache['data']

    tickers = _get_broad_market_tickers()
    if not tickers:
        return None

    try:
        # Batch download - much faster than individual requests
        # Process in chunks to avoid timeout on large lists
        all_changes = pd.Series(dtype=float)
        chunk_size = 300  # Smaller chunks to avoid rate limits
        max_retries = 2
        for i in range(0, len(tickers), chunk_size):
            chunk = tickers[i:i + chunk_size]
            for attempt in range(max_retries + 1):
                try:
                    data = yf.download(chunk, period='2d', auto_adjust=True, threads=True, progress=False, timeout=60)
                    if data.empty:
                        if attempt < max_retries:
                            import time
                            time.sleep(2 * (attempt + 1))  # Back off on empty results
                            continue
                        break
                    close = data['Close'] if isinstance(data.columns, pd.MultiIndex) else data
                    if isinstance(close, pd.Series) or len(close) < 2:
                        break
                    prev_close = close.iloc[-2]
                    curr_close = close.iloc[-1]
                    changes = (curr_close / prev_close - 1).dropna()
                    all_changes = pd.concat([all_changes, changes])
                    break  # Success, move to next chunk
                except Exception as e:
                    err_str = str(e).lower()
                    if 'rate' in err_str and attempt < max_retries:
                        import time
                        time.sleep(3 * (attempt + 1))  # Longer back-off for rate limits
                        continue
                    print(f"Market breadth chunk {i}-{i+chunk_size} error: {e}")
                    break
            # Small delay between chunks to avoid rate limiting
            import time
            time.sleep(0.5)

        if all_changes.empty:
            # Fallback: use v8 API for ALL tickers using parallel requests
            print(f"📊 Market breadth: yf.download failed, trying v8 API fallback for {len(tickers)} stocks...")
            import requests as req_lib
            from concurrent.futures import ThreadPoolExecutor

            _session = req_lib.Session()
            _session.headers.update({
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
            })

            def _fetch_change(sym):
                """Fetch price change via v8 API using meta fields."""
                try:
                    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
                    resp = _session.get(url, params={'range': '1d', 'interval': '1d'}, timeout=8)
                    if resp.status_code != 200:
                        return None
                    meta = (resp.json().get('chart', {}).get('result') or [{}])[0].get('meta', {})
                    price = meta.get('regularMarketPrice')
                    prev = meta.get('chartPreviousClose') or meta.get('previousClose')
                    if price and prev and prev > 0:
                        return (price / prev) - 1
                except Exception:
                    pass
                return None

            with ThreadPoolExecutor(max_workers=20) as pool:
                results = list(pool.map(_fetch_change, tickers))
            changes_list = [r for r in results if r is not None]
            print(f"📊 Market breadth: v8 API got {len(changes_list)}/{len(tickers)} stocks")
            if changes_list:
                all_changes = pd.Series(changes_list)

        if all_changes.empty:
            return None

        advancing = int((all_changes > 0).sum())
        declining = int((all_changes < 0).sum())
        unchanged = int((all_changes == 0).sum())
        total = advancing + declining + unchanged

        # Calculate advance/decline ratio and breadth indicators
        ad_ratio = round(advancing / max(declining, 1), 2)
        breadth_pct = round((advancing / max(total, 1)) * 100, 1)

        result = {
            'advancing': advancing,
            'declining': declining,
            'unchanged': unchanged,
            'total': total,
            'advance_decline_ratio': ad_ratio,
            'breadth_pct': breadth_pct,
            'source': f'US Market ({total} stocks)'
        }

        # Cache the result in memory and on disk
        with _market_breadth_lock:
            _market_breadth_cache['data'] = result
            _market_breadth_cache['timestamp'] = now
        _save_cache_to_disk(_BREADTH_CACHE_FILE, result, now)

        print(f"📊 Market Breadth: {advancing} advancing, {declining} declining "
              f"out of {total} stocks (A/D ratio: {ad_ratio})")
        return result

    except Exception as e:
        print(f"Market breadth calculation error: {e}")
        return None


# Pre-warm dashboard caches on startup in background
def _preload_dashboard_caches():
    """Delay slightly to let the server finish binding, then warm both caches."""
    import time
    time.sleep(5)  # Let server start up first

    # 1. Pre-warm the batch cache if disk cache was stale/missing
    with _batch_cache_lock:
        batch_valid = (_batch_cache['data'] is not None and
                       _batch_cache['timestamp'] is not None and
                       _batch_cache['data'].get('symbols_fetched', 0) > 0)
    if not batch_valid:
        print("📊 Pre-warming dashboard batch cache...")
        try:
            data = _get_batch_dashboard_data()
            if data.get('symbols_fetched', 0) > 0:
                now = datetime.now()
                with _batch_cache_lock:
                    _batch_cache['data'] = data
                    _batch_cache['timestamp'] = now
                _save_cache_to_disk(_BATCH_CACHE_FILE, data, now)
                print(f"📊 Batch cache warmed: {data.get('symbols_fetched', 0)} symbols")
        except Exception as e:
            print(f"⚠️ Batch cache pre-warm failed: {e}")
    else:
        print("📊 Batch cache already warm from disk")

    # 2. Pre-warm market breadth if disk cache was stale/missing
    with _market_breadth_lock:
        breadth_valid = (_market_breadth_cache['data'] is not None and
                         _market_breadth_cache['timestamp'] is not None)
    if not breadth_valid:
        print("📊 Pre-loading market breadth data (1000+ stocks)...")
        calculate_market_breadth()
    else:
        print("📊 Market breadth cache already warm from disk")

threading.Thread(target=_preload_dashboard_caches, daemon=True).start()


# Batch data cache with TTL
_batch_cache = {
    'data': None,
    'timestamp': None,
    'cache_ttl': 300  # seconds (5 min) - reduced API calls significantly
}
_batch_cache_lock = threading.Lock()

# Restore batch cache from disk on import (accept up to 1 hour for quick startup)
_disk_batch, _disk_batch_ts = _load_cache_from_disk(_BATCH_CACHE_FILE, 3600)
if _disk_batch is not None and _disk_batch.get('symbols_fetched', 0) > 0:
    _batch_cache['data'] = _disk_batch
    _batch_cache['timestamp'] = _disk_batch_ts
    print(f"📊 Restored dashboard batch cache from disk (age: {(datetime.now() - _disk_batch_ts).total_seconds():.0f}s, symbols: {_disk_batch.get('symbols_fetched', 0)})")
elif _disk_batch is not None:
    print(f"📊 Skipped stale batch cache from disk (symbols_fetched=0)")

# _fetch_all_quotes_batch is imported from services.market_data

def _get_batch_dashboard_data():
    """Internal function to fetch all dashboard data in one call"""
    # Get market status
    status, status_text = get_market_status()
    
    # Collect ALL symbols we need to fetch
    all_symbols = []
    
    # Index symbols
    index_symbols = list(MAJOR_INDICES.keys())
    all_symbols.extend(index_symbols)
    
    # Sector ETF symbols  
    sector_symbols = list(SECTOR_ETFS.keys())
    all_symbols.extend(sector_symbols)
    
    # Compact movers watchlist (~50 most-liquid names across sectors)
    # Reduced from 189 symbols to cut API payload size
    movers_watchlist = [
        # Tech (10)
        'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'AMD', 'CRM', 'AVGO',
        # Communication (5)
        'NFLX', 'DIS', 'CMCSA', 'T', 'TMUS',
        # Healthcare (5)
        'JNJ', 'UNH', 'LLY', 'PFE', 'ABBV',
        # Financials (5)
        'JPM', 'BAC', 'GS', 'V', 'MA',
        # Consumer (5)
        'WMT', 'HD', 'COST', 'MCD', 'KO',
        # Industrials (5)
        'BA', 'CAT', 'HON', 'UNP', 'GE',
        # Energy (5)
        'XOM', 'CVX', 'COP', 'SLB', 'OXY',
        # EV & Clean Energy (5)
        'RIVN', 'F', 'GM', 'FSLR', 'ENPH',
        # REITs (3)
        'AMT', 'PLD', 'EQIX',
        # High-vol retail favourites (5)
        'PLTR', 'COIN', 'SOFI', 'HOOD', 'SHOP',
    ]
    movers_watchlist = list(set(movers_watchlist))
    all_symbols.extend(movers_watchlist)
    
    # Fetch ALL quotes in one batch call
    all_quotes = _fetch_all_quotes_batch(all_symbols)
    
    # Build indices from cached quotes
    indices = []
    for symbol, name in MAJOR_INDICES.items():
        if symbol in all_quotes:
            quote = all_quotes[symbol].copy()
            quote['name'] = name
            indices.append(quote)
    
    # Build sectors from cached quotes
    sectors = []
    for symbol, sector_name in SECTOR_ETFS.items():
        if symbol in all_quotes:
            quote = all_quotes[symbol]
            sectors.append({
                'sector': sector_name,
                'changePct': quote.get('changePct', 0),
                'price': quote.get('price', 0),
                'symbol': symbol
            })
    sectors = sorted(sectors, key=lambda x: x['changePct'], reverse=True)
    
    # Build movers from cached quotes
    movers_data = []
    for symbol in movers_watchlist:
        if symbol in all_quotes:
            movers_data.append(all_quotes[symbol])
    
    gainers = sorted(
        [m for m in movers_data if m.get('changePct', 0) > 0],
        key=lambda x: x.get('changePct', 0),
        reverse=True
    )[:20]
    
    losers = sorted(
        [m for m in movers_data if m.get('changePct', 0) < 0],
        key=lambda x: x.get('changePct', 0)
    )[:20]
    
    # Get extended hours data based on market status
    extended_hours = None
    if status in ['PRE_MARKET', 'AFTER_HOURS', 'CLOSED']:
        try:
            extended_data = get_premarket_movers(limit=15)
            extended_hours = {
                'gainers': extended_data['gainers'],
                'losers': extended_data['losers'],
                'session': 'pre-market' if status == 'PRE_MARKET' else 'after-hours'
            }
        except Exception as e:
            print(f"Extended hours fetch error: {e}")
    
    # Calculate market pulse from comprehensive US market breadth (1600+ stocks)
    # Serve stale breadth data while refreshing in background — never fall back to
    # the tiny watchlist unless we have zero breadth data at all.
    market_pulse = None
    breadth_stale = False
    with _market_breadth_lock:
        if (_market_breadth_cache['data'] is not None and
            _market_breadth_cache['timestamp'] is not None):
            age = (datetime.now() - _market_breadth_cache['timestamp']).total_seconds()
            if age < _market_breadth_cache.get('cache_ttl', 900):
                market_pulse = _market_breadth_cache['data']
            else:
                # Stale but still usable — serve it and refresh in background
                market_pulse = _market_breadth_cache['data'].copy()
                market_pulse['source'] = market_pulse.get('source', '') + ' (refreshing...)'
                breadth_stale = True

    if breadth_stale:
        # Refresh in background only if not already refreshing
        with _market_breadth_lock:
            if not _market_breadth_cache.get('refreshing'):
                _market_breadth_cache['refreshing'] = True
                def _bg_refresh():
                    try:
                        calculate_market_breadth()
                    finally:
                        with _market_breadth_lock:
                            _market_breadth_cache['refreshing'] = False
                threading.Thread(target=_bg_refresh, daemon=True).start()

    if market_pulse is None:
        # Use fast fallback from already-fetched watchlist data
        advancing = 0
        declining = 0
        unchanged = 0
        for symbol, quote in all_quotes.items():
            pct = quote.get('changePct', 0)
            if pct > 0:
                advancing += 1
            elif pct < 0:
                declining += 1
            else:
                unchanged += 1
        market_pulse = {
            'advancing': advancing,
            'declining': declining,
            'unchanged': unchanged,
            'total': len(all_quotes),
            'advance_decline_ratio': round(advancing / max(declining, 1), 2),
            'breadth_pct': round((advancing / max(len(all_quotes), 1)) * 100, 1),
            'source': 'dashboard watchlist (fallback)'
        }
        # Kick off heavy breadth calculation in background (only if not already running)
        with _market_breadth_lock:
            if not _market_breadth_cache.get('refreshing'):
                _market_breadth_cache['refreshing'] = True
                def _bg_refresh_first():
                    try:
                        calculate_market_breadth()
                    finally:
                        with _market_breadth_lock:
                            _market_breadth_cache['refreshing'] = False
                threading.Thread(target=_bg_refresh_first, daemon=True).start()
    
    return {
        'timestamp': datetime.now().isoformat(),
        'market_status': {
            'status': status,
            'text': status_text
        },
        'indices': indices,
        'sectors': sectors,
        'gainers': gainers,
        'losers': losers,
        'extended_hours': extended_hours,
        'market_pulse': market_pulse,
        'symbols_fetched': len(all_quotes),
        'cached': False
    }

@dashboard_bp.route('/api/dashboard/batch')
def dashboard_batch():
    """
    UNIFIED BATCH ENDPOINT - Returns all dashboard data in a single API call.
    Reduces yfinance API hits by fetching all symbols once and reusing data.
    
    Returns:
        - market_status: Current market status (OPEN/CLOSED/PRE_MARKET/AFTER_HOURS)
        - indices: Major market indices (S&P 500, Dow, NASDAQ, etc.)
        - sectors: Sector ETF performance
        - gainers: Top gaining stocks
        - losers: Top losing stocks
        - extended_hours: Pre-market/after-hours movers (when applicable)
    """
    try:
        # Helper: inject latest breadth data into a response dict
        def _inject_breadth(resp):
            with _market_breadth_lock:
                if (_market_breadth_cache['data'] is not None and
                    _market_breadth_cache['timestamp'] is not None):
                    breadth_age = (datetime.now() - _market_breadth_cache['timestamp']).total_seconds()
                    if breadth_age < _market_breadth_cache.get('cache_ttl', 900):
                        resp['market_pulse'] = _market_breadth_cache['data']
                    elif _market_breadth_cache['data'].get('total', 0) > 100:
                        # Stale breadth still better than nothing
                        stale = _market_breadth_cache['data'].copy()
                        stale['source'] = stale.get('source', '') + ' (stale)'
                        resp['market_pulse'] = stale

        stale_batch = None  # Keep a reference to expired-but-good cache data
        with _batch_cache_lock:
            if _batch_cache['data'] is not None and _batch_cache['timestamp'] is not None:
                age = (datetime.now() - _batch_cache['timestamp']).total_seconds()
                has_data = _batch_cache['data'].get('symbols_fetched', 0) > 0
                if age < _batch_cache['cache_ttl'] and has_data:
                    cached_data = _batch_cache['data'].copy()
                    cached_data['cached'] = True
                    cached_data['cache_age_seconds'] = int(age)
                    _inject_breadth(cached_data)
                    return jsonify({'success': True, **cached_data})
                elif has_data:
                    # Expired but has good data — keep as fallback
                    stale_batch = _batch_cache['data']
        
        # Fetch fresh data
        data = _get_batch_dashboard_data()
        _inject_breadth(data)
        
        # Only cache if we actually got meaningful data
        symbols_fetched = data.get('symbols_fetched', 0)
        if symbols_fetched > 0:
            now = datetime.now()
            with _batch_cache_lock:
                _batch_cache['data'] = data
                _batch_cache['timestamp'] = now
            _save_cache_to_disk(_BATCH_CACHE_FILE, data, now)
        elif stale_batch is not None:
            # Fresh fetch failed (rate-limited) — serve stale good data instead of empty
            print(f"⚠️ Batch fetch returned 0 symbols — serving stale cache ({stale_batch.get('symbols_fetched', 0)} symbols)")
            data = stale_batch.copy()
            data['cached'] = True
            data['stale'] = True
            _inject_breadth(data)
        else:
            print(f"⚠️ Batch dashboard returned 0 symbols — no stale fallback available")
        
        return jsonify({'success': True, **data})
    
    except Exception as e:
        print(f"Error in batch dashboard API: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

@dashboard_bp.route('/api/dashboard/batch', methods=['POST'])
def dashboard_batch_custom():
    """
    Custom batch endpoint - fetch only specific data sections.
    
    POST body (JSON):
        {
            "sections": ["indices", "sectors", "gainers", "losers", "extended_hours"],
            "symbols": ["AAPL", "MSFT"]  // Optional: additional symbols to fetch
        }
    """
    try:
        req = request.get_json(force=True) or {}
        sections = req.get('sections', ['indices', 'sectors', 'gainers', 'losers'])
        extra_symbols = req.get('symbols', [])
        
        result = {
            'timestamp': datetime.now().isoformat(),
            'success': True
        }
        
        all_symbols = list(extra_symbols)
        
        # Collect symbols based on requested sections
        if 'indices' in sections:
            all_symbols.extend(MAJOR_INDICES.keys())
        if 'sectors' in sections:
            all_symbols.extend(SECTOR_ETFS.keys())
        if 'gainers' in sections or 'losers' in sections:
            all_symbols.extend([
                'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'AMD',
                'NFLX', 'DIS', 'PYPL', 'INTC', 'BABA', 'PFE', 'WMT', 'JPM',
                'BAC', 'XOM', 'CVX', 'T', 'VZ', 'KO', 'PEP', 'NKE', 'BA'
            ])
        
        # Fetch all at once
        all_quotes = _fetch_all_quotes_batch(all_symbols)
        
        # Build response based on sections
        if 'indices' in sections:
            indices = []
            for symbol, name in MAJOR_INDICES.items():
                if symbol in all_quotes:
                    quote = all_quotes[symbol].copy()
                    quote['name'] = name
                    indices.append(quote)
            result['indices'] = indices
        
        if 'sectors' in sections:
            sectors = []
            for symbol, sector_name in SECTOR_ETFS.items():
                if symbol in all_quotes:
                    sectors.append({
                        'sector': sector_name,
                        'changePct': all_quotes[symbol].get('changePct', 0),
                        'price': all_quotes[symbol].get('price', 0),
                        'symbol': symbol
                    })
            result['sectors'] = sorted(sectors, key=lambda x: x['changePct'], reverse=True)
        
        if 'gainers' in sections or 'losers' in sections:
            movers = [q for q in all_quotes.values() if q.get('changePct') is not None]
            if 'gainers' in sections:
                result['gainers'] = sorted(
                    [m for m in movers if m.get('changePct', 0) > 0],
                    key=lambda x: x.get('changePct', 0), reverse=True
                )[:20]
            if 'losers' in sections:
                result['losers'] = sorted(
                    [m for m in movers if m.get('changePct', 0) < 0],
                    key=lambda x: x.get('changePct', 0)
                )[:20]
        
        # Custom symbols
        if extra_symbols:
            result['custom_quotes'] = {sym: all_quotes.get(sym) for sym in extra_symbols if sym in all_quotes}
        
        status, status_text = get_market_status()
        result['market_status'] = {'status': status, 'text': status_text}
        
        return jsonify(result)
    
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# ============================================================================
# INDIVIDUAL MARKET ENDPOINTS (kept for backward compatibility)
# ============================================================================

@dashboard_bp.route('/api/market/overview')
def market_overview():
    """Get market indices and status"""
    try:
        status, status_text = get_market_status()
        
        # Batch fetch all index symbols in one call
        index_symbols = list(MAJOR_INDICES.keys())
        all_quotes = _fetch_all_quotes_batch(index_symbols)
        
        indices = []
        for symbol, name in MAJOR_INDICES.items():
            if symbol in all_quotes:
                quote = all_quotes[symbol].copy()
                quote['name'] = name
                indices.append(quote)
        
        return jsonify({
            'success': True,
            'timestamp': datetime.now().isoformat(),
            'market_status': {
                'status': status,
                'text': status_text
            },
            'marketStatus': status,
            'marketStatusText': status_text,
            'indices': indices
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@dashboard_bp.route('/api/market/sectors')
def market_sectors():
    """Get sector performance heatmap data"""
    try:
        sectors = get_sector_performance()
        return jsonify({
            'success': True,
            'timestamp': datetime.now().isoformat(),
            'sectors': sectors
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@dashboard_bp.route('/api/market/sector/<sector_etf>/stocks')
def sector_stocks(sector_etf):
    """Get stocks for a specific sector with live quotes"""
    try:
        sector_etf = sector_etf.upper()
        if sector_etf not in SECTOR_STOCKS:
            return jsonify({'success': False, 'error': f'Unknown sector ETF: {sector_etf}'}), 400
        
        sector_name = SECTOR_ETFS.get(sector_etf, sector_etf)
        symbols = SECTOR_STOCKS[sector_etf]
        quotes_map = _fetch_all_quotes_batch(symbols)
        stocks = []

        for symbol in symbols:
            quote = quotes_map.get(symbol)
            if not quote:
                continue
            info = _get_cached_info_only(symbol)
            stocks.append({
                'symbol': symbol,
                'name': info.get('shortName', symbol) if isinstance(info, dict) else symbol,
                'price': float(quote.get('price', 0) or 0),
                'change': float(quote.get('change', 0) or 0),
                'changePct': float(quote.get('changePct', 0) or 0),
                'volume': int(quote.get('volume', 0) or 0),
                'marketCap': info.get('marketCap', 0) if isinstance(info, dict) else 0,
                'high': float(quote.get('high', 0) or 0),
                'low': float(quote.get('low', 0) or 0)
            })

        # Sort by market cap descending
        stocks.sort(key=lambda x: x.get('marketCap', 0), reverse=True)
        
        return jsonify({
            'success': True,
            'timestamp': datetime.now().isoformat(),
            'sector': sector_name,
            'sectorETF': sector_etf,
            'stocks': stocks
        })
    except Exception as e:
        print(f"Error fetching sector stocks: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@dashboard_bp.route('/api/market/movers/<direction>')
def market_movers(direction):
    """Get top gainers or losers"""
    try:
        if direction not in ['gainers', 'losers']:
            return jsonify({'success': False, 'error': 'Invalid direction'}), 400
        
        movers = get_top_movers(direction=direction, limit=20)
        return jsonify({
            'success': True,
            'timestamp': datetime.now().isoformat(),
            'direction': direction,
            'movers': movers
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@dashboard_bp.route('/api/market/premarket')
def premarket_analysis():
    """Get pre-market top movers analysis"""
    try:
        market_state, market_desc = get_market_status()
        data = get_premarket_movers(limit=20)
        
        return jsonify({
            'success': True,
            'timestamp': datetime.now().isoformat(),
            'marketState': market_state,
            'marketDescription': market_desc,
            'gainers': data['gainers'],
            'losers': data['losers'],
            'totalScanned': data['total_scanned']
        })
    except Exception as e:
        print(f"Error in pre-market analysis: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@dashboard_bp.route('/api/market/afterhours')
def afterhours_analysis():
    """Get after-hours top movers analysis"""
    try:
        market_state, market_desc = get_market_status()
        data = get_afterhours_movers(limit=20)
        
        return jsonify({
            'success': True,
            'timestamp': datetime.now().isoformat(),
            'marketState': market_state,
            'marketDescription': market_desc,
            'gainers': data['gainers'],
            'losers': data['losers'],
            'totalScanned': data['total_scanned']
        })
    except Exception as e:
        print(f"Error in after-hours analysis: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@dashboard_bp.route('/api/market/52week')
def week_52_extremes():
    """Get stocks touching 52-week highs and lows"""
    try:
        # Popular stocks to scan for 52-week extremes
        scan_symbols = [
            'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'META', 'NVDA', 'TSLA', 'AMD', 'INTC', 'NFLX',
            'DIS', 'BA', 'JPM', 'GS', 'V', 'MA', 'PYPL', 'COIN', 'SHOP',
            'CRM', 'ORCL', 'ADBE', 'NOW', 'SNOW', 'PLTR', 'UBER', 'LYFT', 'ABNB', 'RIVN',
            'F', 'GM', 'NIO', 'LCID', 'XOM', 'CVX', 'COP', 'OXY', 'DVN',
            'WMT', 'COST', 'TGT', 'HD', 'LOW', 'NKE', 'LULU', 'SBUX', 'MCD', 'CMG',
            'PFE', 'JNJ', 'UNH', 'ABBV', 'LLY', 'MRK', 'BMY', 'GILD', 'MRNA', 'BNTX',
            'SPY', 'QQQ', 'IWM', 'DIA', 'VTI', 'XLF', 'XLE', 'XLK', 'XLV', 'ARKK'
        ]
        
        highs = []
        lows = []
        
        def analyze_52week(symbol):
            try:
                hist = cached_get_history(symbol, period='1y', interval='1d')
                if hist is None or hist.empty or len(hist) < 50:
                    return None
                    
                current_price = hist['Close'].iloc[-1]
                high_52w = hist['High'].max()
                low_52w = hist['Low'].min()
                prev_close = hist['Close'].iloc[-2] if len(hist) > 1 else current_price
                
                # Calculate distance from extremes
                pct_from_high = ((current_price - high_52w) / high_52w) * 100
                pct_from_low = ((current_price - low_52w) / low_52w) * 100
                change_pct = ((current_price - prev_close) / prev_close) * 100
                
                info = cached_get_ticker_info(symbol)
                result = {
                    'symbol': symbol,
                    'name': info.get('shortName', symbol)[:30],
                    'price': float(current_price),
                    'change_pct': float(change_pct),
                    'high_52w': float(high_52w),
                    'low_52w': float(low_52w),
                    'pct_from_high': float(pct_from_high),
                    'pct_from_low': float(pct_from_low),
                    'volume': int(hist['Volume'].iloc[-1]) if 'Volume' in hist else 0
                }
                
                # Near 52-week high (within 3%)
                if pct_from_high >= -3:
                    result['is_high'] = True
                    return result
                # Near 52-week low (within 3%)  
                elif pct_from_low <= 3:
                    result['is_low'] = True
                    return result
                return None
            except Exception as e:
                return None
        
        with ThreadPoolExecutor(max_workers=15) as executor:
            results = list(executor.map(analyze_52week, scan_symbols))
        
        for r in results:
            if r:
                if r.get('is_high'):
                    highs.append(r)
                elif r.get('is_low'):
                    lows.append(r)
        
        # Sort: highs by closest to high, lows by closest to low
        highs.sort(key=lambda x: x['pct_from_high'], reverse=True)
        lows.sort(key=lambda x: x['pct_from_low'])
        
        return jsonify({
            'success': True,
            'timestamp': datetime.now().isoformat(),
            'highs': highs[:15],
            'lows': lows[:15],
            'total_scanned': len(scan_symbols)
        })
    except Exception as e:
        print(f"Error in 52-week analysis: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@dashboard_bp.route('/api/market/crashed')
def crashed_stocks():
    """Get stocks crashed 30%+ from their 52-week high - potential value plays or falling knives"""
    try:
        # Extended list of stocks to scan for crashes
        scan_symbols = [
            # Big Tech
            'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'META', 'NVDA', 'TSLA', 'AMD', 'INTC', 'NFLX',
            # Financials
            'JPM', 'BAC', 'GS', 'MS', 'WFC', 'C', 'SCHW', 'BLK', 'V', 'MA',
            # Tech/Software
            'CRM', 'ORCL', 'ADBE', 'NOW', 'SNOW', 'PLTR', 'DDOG', 'MDB', 'NET', 'CRWD',
            'ZS', 'OKTA', 'TWLO', 'DOCU', 'ZM', 'ROKU', 'SHOP', 'PYPL', 'SOFI',
            # Consumer/Retail
            'DIS', 'SBUX', 'NKE', 'LULU', 'TGT', 'COST', 'WMT', 'HD', 'LOW', 'CMG',
            # EV & Auto
            'RIVN', 'LCID', 'NIO', 'XPEV', 'LI', 'F', 'GM', 'CVNA',
            # Healthcare/Biotech
            'PFE', 'MRNA', 'BNTX', 'BIIB', 'GILD', 'REGN', 'VRTX', 'ILMN', 'TDOC',
            # Crypto/Fintech
            'COIN', 'MSTR', 'HOOD', 'AFRM', 'UPST',
            # Growth/Speculative
            'ARKK', 'ARKG', 'ARKF', 'PATH', 'RBLX', 'U', 'SNAP', 'PINS', 'MTCH',
            # Energy
            'XOM', 'CVX', 'COP', 'OXY', 'DVN', 'FANG', 'EOG',
            # Semiconductors
            'QCOM', 'AVGO', 'MU', 'MRVL', 'ON', 'SWKS', 'QRVO', 'LRCX', 'AMAT', 'KLAC',
            # Other popular
            'BA', 'CAT', 'DE', 'MMM', 'RTX', 'LMT', 'NOC', 'GE', 'HON',
            'ABNB', 'UBER', 'LYFT', 'DASH', 'GRAB', 'SE'
        ]
        
        crashed_stocks_list = []
        
        def analyze_crash(symbol):
            try:
                # Use prepost=True for live prices during extended hours
                hist = cached_get_history(symbol, period='1y', interval='1d', prepost=True)
                if hist is None or hist.empty or len(hist) < 50:
                    return None
                    
                current_price = hist['Close'].iloc[-1]
                high_52w = hist['High'].max()
                low_52w = hist['Low'].min()
                prev_close = hist['Close'].iloc[-2] if len(hist) > 1 else current_price
                
                # Calculate % drop from 52-week high
                pct_from_high = ((current_price - high_52w) / high_52w) * 100
                pct_from_low = ((current_price - low_52w) / low_52w) * 100
                change_pct = ((current_price - prev_close) / prev_close) * 100
                
                # Only include stocks that crashed 30%+ from 52-week high
                if pct_from_high <= -30:
                    info = cached_get_ticker_info(symbol)
                    market_cap = info.get('marketCap', 0)
                    
                    # Format market cap
                    if market_cap >= 1e12:
                        market_cap_str = f"${market_cap/1e12:.1f}T"
                    elif market_cap >= 1e9:
                        market_cap_str = f"${market_cap/1e9:.1f}B"
                    elif market_cap >= 1e6:
                        market_cap_str = f"${market_cap/1e6:.0f}M"
                    else:
                        market_cap_str = "N/A"
                    
                    return {
                        'symbol': symbol,
                        'name': info.get('shortName', symbol)[:35],
                        'price': float(current_price),
                        'change_pct': float(change_pct),
                        'high_52w': float(high_52w),
                        'low_52w': float(low_52w),
                        'pct_from_high': float(pct_from_high),
                        'pct_from_low': float(pct_from_low),
                        'market_cap': market_cap,
                        'market_cap_str': market_cap_str,
                        'volume': int(hist['Volume'].iloc[-1]) if 'Volume' in hist else 0,
                        'sector': info.get('sector', 'N/A'),
                        'pe_ratio': info.get('trailingPE', None),
                    }
                return None
            except Exception as e:
                return None
        
        with ThreadPoolExecutor(max_workers=15) as executor:
            results = list(executor.map(analyze_crash, scan_symbols))
        
        for r in results:
            if r:
                crashed_stocks_list.append(r)
        
        # Sort by largest crash first (most negative pct_from_high)
        crashed_stocks_list.sort(key=lambda x: x['pct_from_high'])
        
        return jsonify({
            'success': True,
            'timestamp': datetime.now().isoformat(),
            'stocks': crashed_stocks_list[:30],  # Top 30 crashed stocks
            'total_crashed': len(crashed_stocks_list),
            'total_scanned': len(scan_symbols),
            'threshold': -30  # 30% crash threshold
        })
    except Exception as e:
        print(f"Error in crashed stocks analysis: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

