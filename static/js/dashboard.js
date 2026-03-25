// ============================================================================
// US Market Trading Dashboard - JavaScript
// Real-time updates, WebSocket connections, API integration
// ============================================================================

// ── Resilient fetch with timeout, retry, and auto-reconnect ──
let _dashServerOnline = true;
let _dashReconnectTimer = null;

async function resilientFetch(url, options = {}, {
    timeoutMs = 30000,
    retries = 2,
    retryDelay = 1000,
    label = url
} = {}) {
    for (let attempt = 0; attempt <= retries; attempt++) {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), timeoutMs);
        try {
            const resp = await fetch(url, { ...options, signal: controller.signal });
            clearTimeout(timer);
            if (!_dashServerOnline) {
                _dashServerOnline = true;
                console.log('🟢 Server reconnected');
            }
            return resp;
        } catch (err) {
            clearTimeout(timer);
            const isTimeout = err.name === 'AbortError';
            const isNetwork = err.message === 'Failed to fetch' || err.name === 'TypeError';
            if ((isTimeout || isNetwork) && attempt < retries) {
                console.warn(`⏳ ${label} attempt ${attempt + 1} failed, retrying...`);
                await new Promise(r => setTimeout(r, retryDelay));
                retryDelay *= 2;
                continue;
            }
            if (isNetwork && _dashServerOnline) {
                _dashServerOnline = false;
                console.error('🔴 Server unreachable');
                _startDashReconnect();
            }
            throw err;
        }
    }
}

function _startDashReconnect() {
    if (_dashReconnectTimer) return;
    _dashReconnectTimer = setInterval(async () => {
        try {
            const r = await fetch('/api/health', { cache: 'no-store' });
            if (r.ok) {
                _dashServerOnline = true;
                clearInterval(_dashReconnectTimer);
                _dashReconnectTimer = null;
                console.log('🟢 Server back online');
            }
        } catch (_) {}
    }, 5000);
}

class TradingDashboard {
    constructor(autoInit = true) {
        this.socket = null;
        this.subscribedSymbols = [];
        this.refreshInterval = null;
        
        // Always initialize basic connection and event listeners
        console.log('🚀 Initializing Trading Dashboard... autoInit =', autoInit);
        this.connectWebSocket();
        this.setupEventListeners();
        
        // Only load dashboard-specific data if requested (for main dashboard page)
        if (autoInit) {
            console.log('✅ autoInit=true, will load dashboard data');
            this.loadDashboardData();
        } else {
            console.log('⏭️ autoInit=false, skipping dashboard data load');
        }
    }

    loadDashboardData() {
        // Only load these if we're on the dashboard page (elements exist)
        const sectorHeatmap = document.getElementById('sector-heatmap');
        console.log('🎯 loadDashboardData called, sector-heatmap exists:', !!sectorHeatmap);
        
        if (sectorHeatmap) {
            console.log('📊 Loading all dashboard data via BATCH API (single request)...');
            // Use batch API to reduce API calls from 6+ to 1
            this.loadDashboardBatch();
            this.startAutoRefresh();
        } else {
            console.log('⚠️ Skipping dashboard data load - not on dashboard page');
        }
    }

    // ========================================================================
    // BATCH API - Single request for all dashboard data
    // ========================================================================
    
    async loadDashboardBatch() {
        try {
            console.log('🚀 Fetching all dashboard data in single batch request...');
            const startTime = performance.now();
            
            const response = await resilientFetch(`/api/dashboard/batch?_ts=${Date.now()}`, {
                cache: 'no-store'
            }, { timeoutMs: 30000, retries: 2, label: 'dashboardBatch' });
            const data = await response.json();
            
            const elapsed = (performance.now() - startTime).toFixed(0);
            console.log(`✅ Batch API completed in ${elapsed}ms, cached: ${data.cached || false}`);
            
            if (data.success) {
                // Render all sections from batch response
                if (data.market_status) {
                    this.renderMarketStatus({
                        marketStatus: data.market_status.status,
                        marketStatusText: data.market_status.text,
                        timestamp: data.timestamp
                    });
                }
                
                if (data.indices && data.indices.length > 0) {
                    this.renderIndices(data.indices);
                    // Subscribe to real-time updates for indices
                    const indexSymbols = data.indices.map(idx => idx.symbol);
                    this.subscribeToQuotes(indexSymbols);
                }
                
                if (data.sectors && data.sectors.length > 0) {
                    this.renderSectorHeatmap(data.sectors);
                }
                
                if (data.gainers && data.gainers.length > 0) {
                    this.renderMoversTable(data.gainers, 'gainers');
                }
                
                if (data.losers && data.losers.length > 0) {
                    this.renderMoversTable(data.losers, 'losers');
                }
                
                if (data.extended_hours) {
                    this.renderExtendedHoursFromBatch(data.extended_hours, data.market_status);
                }
                
                // Update Market Pulse stats (advancing/declining counts)               
                if (data.market_pulse) {
                    this.updateMarketPulse(data.market_pulse);
                }
                
                // Show market closed banner if applicable
                if (data.market_status) {
                    this.updateMarketClosedBanner(data.market_status);
                }
                
                // Load volume spike separately (heavy scanner, has its own cache)
                this.loadVolumeSpikeScanner();
                
                console.log(`📊 Dashboard rendered: ${data.symbols_fetched || 'N/A'} symbols fetched`);
            } else {
                console.error('❌ Batch API failed:', data.error);
                // Fallback to individual calls
                this.loadDashboardDataLegacy();
            }
        } catch (error) {
            console.error('❌ Batch API error, falling back to individual calls:', error);
            this.loadDashboardDataLegacy();
        }
    }
    
    renderExtendedHoursFromBatch(extendedData, marketStatus) {
        const container = document.getElementById('extended-hours-status');
        if (!container) return;
        
        const sessionLabel = extendedData.session === 'pre-market' ? 'Pre-Market' : 'After-Hours';
        
        if (!extendedData.gainers?.length && !extendedData.losers?.length) {
            container.innerHTML = `
                <div style="color: var(--text-secondary); padding: 20px; text-align: center;">
                    ⚠️ Extended hours data not available
                </div>
            `;
            return;
        }
        
        this.renderExtendedHoursAnalysis({
            success: true,
            gainers: extendedData.gainers || [],
            losers: extendedData.losers || []
        }, sessionLabel);
    }
    
    loadDashboardDataLegacy() {
        // Fallback: Original individual API calls (kept for compatibility)
        console.log('⚠️ Using legacy individual API calls...');
        this.loadMarketOverview();
        this.loadSectorPerformance();
        this.loadTopMovers('gainers');
        this.loadTopMovers('losers');
        this.loadExtendedHoursAnalysis();
        this.loadVolumeSpikeScanner();
    }

    // ========================================================================
    // WEBSOCKET CONNECTION
    // ========================================================================

    connectWebSocket() {
        this.socket = io();
        
        this.socket.on('connect', () => {
            console.log('✅ WebSocket connected');
            this.updateConnectionStatus('connected');
        });

        this.socket.on('disconnect', () => {
            console.log('❌ WebSocket disconnected');
            this.updateConnectionStatus('disconnected');
        });

        this.socket.on('quote_update', (data) => {
            this.handleQuoteUpdate(data);
        });

        this.socket.on('connection_status', (data) => {
            console.log('Connection status:', data);
        });
        
        // When backend notifies that positions changed on disk, refresh positions
        this.socket.on('positions_updated', (data) => {
            console.log('🔁 positions_updated received, reloading active positions', data);
            this.loadActivePositions();
        });
    }

    updateConnectionStatus(status) {
        const statusEl = document.getElementById('connection-status');
        if (statusEl) {
            statusEl.className = status === 'connected' ? 'status-dot open' : 'status-dot closed';
        }
    }

    subscribeToQuotes(symbols) {
        if (this.socket && this.socket.connected) {
            this.subscribedSymbols = symbols;
            this.socket.emit('subscribe_quotes', { symbols });
            console.log('📊 Subscribed to:', symbols);
        }
    }

    handleQuoteUpdate(data) {
        data.quotes.forEach(quote => {
            this.updateQuoteDisplay(quote);
        });
    }

    updateQuoteDisplay(quote) {
        const elements = document.querySelectorAll(`[data-symbol="${quote.symbol}"]`);
        elements.forEach(el => {
            const priceEl = el.querySelector('.price');
            const changeEl = el.querySelector('.change');
            
            if (priceEl) {
                priceEl.textContent = `$${quote.price.toFixed(2)}`;
                priceEl.classList.add('fade-in');
            }
            
            if (changeEl && quote.change !== undefined) {
                const changeText = quote.change >= 0 ? 
                    `+${quote.change.toFixed(2)} (+${quote.changePct.toFixed(2)}%)` :
                    `${quote.change.toFixed(2)} (${quote.changePct.toFixed(2)}%)`;
                changeEl.textContent = changeText;
                changeEl.className = quote.change >= 0 ? 'change positive' : 'change negative';
            }

            // Show real-time source indicator
            const sourceEl = el.querySelector('.data-source');
            if (sourceEl && quote.source) {
                const isRealtime = quote.source.startsWith('alpaca');
                sourceEl.textContent = isRealtime ? '⚡ Live' : '📊 Delayed';
                sourceEl.className = `data-source ${isRealtime ? 'realtime' : 'delayed'}`;
            }
        });
    }

    // ========================================================================
    // API CALLS
    // ========================================================================

    async loadMarketOverview() {
        try {
            const response = await fetch('/api/market/overview');
            const data = await response.json();
            
            if (data.success) {
                this.renderMarketStatus(data);
                this.renderIndices(data.indices);
                
                // Subscribe to index updates
                const indexSymbols = data.indices.map(idx => idx.symbol);
                this.subscribeToQuotes(indexSymbols);
            }
        } catch (error) {
            console.error('Error loading market overview:', error);
            this.showError('Failed to load market data');
        }
    }

    async loadSectorPerformance() {
        try {
            this.showLoading('sector-heatmap');
            const response = await fetch('/api/market/sectors');
            const data = await response.json();
            
            console.log('📊 Sector Performance Data:', data);
            
            if (data.success && data.sectors && data.sectors.length > 0) {
                console.log(`✅ Rendering ${data.sectors.length} sectors`);
                this.renderSectorHeatmap(data.sectors);
            } else {
                console.warn('⚠️ No sector data received');
            }
        } catch (error) {
            console.error('❌ Error loading sectors:', error);
            this.showError('Failed to load sector data');
        }
    }

    async loadTopMovers(direction = 'gainers') {
        try {
            this.showLoading('movers-table');
            const response = await fetch(`/api/market/movers/${direction}`);
            const data = await response.json();
            
            if (data.success) {
                this.renderMoversTable(data.movers, direction);
            }
        } catch (error) {
            console.error('Error loading movers:', error);
            this.showError('Failed to load movers data');
        }
    }

    async loadExtendedHoursAnalysis() {
        try {
            console.log('🌅 Loading extended hours analysis...');
            
            // Check market status first
            const overviewResponse = await fetch('/api/market/overview');
            const overviewData = await overviewResponse.json();
            
            let endpoint = '/api/market/premarket';
            let sessionLabel = 'Pre-Market';
            
            if (overviewData.success && overviewData.market_status) {
                const status = overviewData.market_status.status;
                if (status === 'AFTER_HOURS') {
                    endpoint = '/api/market/afterhours';
                    sessionLabel = 'After-Hours';
                } else if (status === 'OPEN') {
                    endpoint = '/api/market/premarket'; // Still show pre-market data during regular hours
                    sessionLabel = 'Extended Hours (Pre-Market Data)';
                }
            }
            
            const response = await fetch(endpoint);
            const data = await response.json();
            
            if (data.success) {
                this.renderExtendedHoursAnalysis(data, sessionLabel);
            } else {
                console.warn('Extended hours data not available');
                document.getElementById('extended-hours-status').innerHTML = `
                    <div style="color: var(--text-secondary); padding: 20px;">
                        ⚠️ Extended hours data not available
                    </div>
                `;
            }
        } catch (error) {
            console.error('Error loading extended hours analysis:', error);
            document.getElementById('extended-hours-status').innerHTML = `
                <div style="color: var(--text-secondary); padding: 20px;">
                    ⚠️ Failed to load extended hours data
                </div>
            `;
        }
    }

    async loadScanner(scannerType, callback, retryCount = 0) {
        const MAX_RETRIES = 20; // Max 20 retries (~60 seconds)
        try {
            this.showLoading('scanner-results');
            const response = await resilientFetch(`/api/scanner/${scannerType}?_ts=${Date.now()}`, {
                cache: 'no-store'
            }, { timeoutMs: 60000, retries: 2, label: `scanner-${scannerType}` });
            const data = await response.json();
            
            if (data.success) {
                // Check if scanner is still running
                if (data.scanning || (data.picks && Object.values(data.picks).every(arr => arr.length === 0))) {
                    if (retryCount >= MAX_RETRIES) {
                        const container = document.getElementById('scanner-results');
                        if (container) {
                            container.innerHTML = `
                                <div style="color: var(--text-secondary); padding: 20px; text-align: center;">
                                    <strong>⚠️ Scanner timed out</strong><br>
                                    <small>No results found after scanning. Try refreshing the page.</small>
                                </div>
                            `;
                        }
                        return;
                    }
                    const container = document.getElementById('scanner-results');
                    if (container) {
                        container.innerHTML = `
                            <div class="loading">
                                <div class="spinner"></div>
                                <div>
                                    <strong>Scanning market...</strong><br>
                                    <small style="color: var(--text-secondary);">${data.message || 'This may take 3-5 seconds'}</small>
                                </div>
                            </div>
                        `;
                    }
                    
                    // Auto-retry after 3 seconds
                    setTimeout(() => {
                        console.log(`🔄 Retrying ${scannerType} scanner... (attempt ${retryCount + 1}/${MAX_RETRIES})`);
                        this.loadScanner(scannerType, callback, retryCount + 1);
                    }, 3000);
                } else {
                    this.renderScannerResults(data, scannerType);
                    if (typeof callback === 'function') {
                        const results = data.picks ? [].concat(...Object.values(data.picks)) : (data.stocks || []);
                        callback(results);
                    }
                }
            }
        } catch (error) {
            console.error(`Error loading ${scannerType} scanner:`, error);
            this.showError(`Failed to load ${scannerType} results`);
        }
    }

    async loadActivePositions(forceRefresh = false) {
        console.log('📍 loadActivePositions called, forceRefresh:', forceRefresh);
        try {
            const params = forceRefresh ? `force_live=1&_ts=${Date.now()}` : `_ts=${Date.now()}`;
            const response = await resilientFetch(`/api/positions/active?${params}`, {
                cache: 'no-store'
            }, { timeoutMs: 30000, retries: 2, label: 'activePositions' });
            const data = await response.json();
            console.log('📍 Positions API response:', data);
            
            if (data.success) {
                console.log(`✅ Rendering ${data.positions.length} active positions, ${data.closed_positions.length} closed`);
                this.renderActivePositions(data.positions, data.stats);
                this.renderClosedPositions(data.closed_positions, data.stats);
                this.renderPerformanceStats(data.positions, data.closed_positions, data.stats);
            } else {
                console.warn('⚠️ Positions API returned success=false');
            }
        } catch (error) {
            console.error('❌ Error loading positions:', error);
            this.showError('Failed to load positions');
        }
    }

    async loadVolumeSpikeScanner() {
        console.log('📊 loadVolumeSpikeScanner called');
        const container = document.getElementById('volume-spike-container');
        if (!container) {
            console.log('⚠️ volume-spike-container not found');
            return;
        }

        try {
            const response = await resilientFetch(`/api/scanner/volume-spike?_ts=${Date.now()}`, {
                cache: 'no-store'
            }, { timeoutMs: 60000, retries: 2, label: 'volumeSpike' });
            const data = await response.json();
            
            if (data.success && data.stocks && data.stocks.length > 0) {
                console.log(`✅ Volume spike scanner found ${data.stocks.length} stocks`);
                this.renderVolumeSpikeResults(data.stocks, container);
            } else if (data.scanning) {
                container.innerHTML = `
                    <div style="padding: 20px; text-align: center; color: var(--text-secondary);">
                        <div class="loading">
                            <div class="spinner"></div>
                            <div>${data.message || 'Scanner running in background...'}</div>
                        </div>
                    </div>
                `;
                // Retry after delay
                setTimeout(() => this.loadVolumeSpikeScanner(), 30000);
            } else {
                container.innerHTML = `
                    <div style="padding: 20px; text-align: center; color: var(--text-secondary);">
                        ℹ️ No unusual volume spikes detected at this time.
                    </div>
                `;
            }
        } catch (error) {
            console.error('❌ Error loading volume spike scanner:', error);
            container.innerHTML = `
                <div style="padding: 20px; text-align: center; color: var(--accent-red);">
                    ❌ Error loading volume spike data
                </div>
            `;
        }
    }

    renderVolumeSpikeResults(stocks, container) {
        const html = `
            <div style="overflow-x: auto;">
                <table class="data-table" style="width: 100%; background: var(--bg-card); border-radius: 8px;">
                    <thead>
                        <tr>
                            <th style="text-align: left; padding: 12px;">Symbol</th>
                            <th style="text-align: left; padding: 12px;">Company</th>
                            <th style="text-align: center; padding: 12px;">Direction</th>
                            <th style="text-align: right; padding: 12px;">Price</th>
                            <th style="text-align: right; padding: 12px;">Change %</th>
                            <th style="text-align: right; padding: 12px;">Volume</th>
                            <th style="text-align: right; padding: 12px;">Avg Volume</th>
                            <th style="text-align: right; padding: 12px;">Vol Ratio</th>
                            <th style="text-align: right; padding: 12px;">Mkt Cap</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${stocks.map(stock => {
                            const changeColor = stock.price_change_pct > 0 ? 'var(--accent-green)' : 'var(--accent-red)';
                            const directionColor = stock.direction === 'BULLISH' ? 'var(--accent-green)' : 'var(--accent-red)';
                            const volRatioColor = stock.volume_ratio >= 5 ? 'var(--accent-green)' : stock.volume_ratio >= 3 ? '#ffa500' : '#ffcc00';
                            
                            return `
                                <tr style="border-bottom: 1px solid var(--border-color);">
                                    <td style="padding: 12px;">
                                        <strong style="color: var(--accent-blue); font-size: 14px;">${stock.symbol}</strong>
                                    </td>
                                    <td style="padding: 12px; color: var(--text-secondary); font-size: 12px;">
                                        ${stock.company_name}
                                    </td>
                                    <td style="text-align: center; padding: 12px;">
                                        <div style="display: inline-block; padding: 3px 8px; background: ${directionColor}22; border-radius: 8px; font-weight: 600; color: ${directionColor}; font-size: 11px;">
                                            ${stock.direction === 'BULLISH' ? '📈 UP' : '📉 DOWN'}
                                        </div>
                                    </td>
                                    <td style="text-align: right; padding: 12px; font-weight: 600;">
                                        $${stock.price.toFixed(2)}
                                    </td>
                                    <td style="text-align: right; padding: 12px; font-weight: 600; color: ${changeColor};">
                                        ${stock.price_change_pct >= 0 ? '+' : ''}${stock.price_change_pct.toFixed(2)}%
                                    </td>
                                    <td style="text-align: right; padding: 12px; color: var(--text-secondary);">
                                        ${this.formatVolume(stock.volume)}
                                    </td>
                                    <td style="text-align: right; padding: 12px; color: var(--text-secondary); font-size: 12px;">
                                        ${this.formatVolume(stock.avg_volume)}
                                    </td>
                                    <td style="text-align: right; padding: 12px;">
                                        <span style="color: ${volRatioColor}; font-weight: 600; padding: 4px 8px; background: ${volRatioColor}22; border-radius: 6px;">
                                            ${stock.volume_ratio.toFixed(1)}x
                                        </span>
                                    </td>
                                    <td style="text-align: right; padding: 12px; color: var(--text-secondary); font-size: 12px;">
                                        $${(stock.market_cap / 1e9).toFixed(2)}B
                                    </td>
                                </tr>
                            `;
                        }).join('')}
                    </tbody>
                </table>
            </div>
        `;
        container.innerHTML = html;
    }

    // ========================================================================
    // RENDERING METHODS
    // ========================================================================

    renderMarketStatus(data) {
        const statusEl = document.getElementById('market-status');
        if (!statusEl) return;

        const statusClass = data.marketStatus.toLowerCase().replace('_', '-');
        const statusDot = `<span class="status-dot ${statusClass}"></span>`;
        
        statusEl.innerHTML = `
            <div class="status-indicator">
                ${statusDot}
                <div>
                    <strong>${data.marketStatusText}</strong>
                    <div style="font-size: 12px; color: var(--text-secondary);">
                        ${new Date(data.timestamp).toLocaleTimeString()}
                    </div>
                </div>
            </div>
        `;
    }

    renderIndices(indices) {
        const container = document.getElementById('indices-strip');
        if (!container) return;

        container.innerHTML = indices.map(idx => `
            <div class="index-item" data-symbol="${idx.symbol}">
                <div class="index-name">${idx.name}</div>
                <div class="index-value price">$${idx.price.toFixed(2)}</div>
                <div class="index-change ${idx.change >= 0 ? 'positive' : 'negative'} change">
                    ${idx.change >= 0 ? '+' : ''}${idx.change.toFixed(2)} 
                    (${idx.changePct >= 0 ? '+' : ''}${idx.changePct.toFixed(2)}%)
                </div>
            </div>
        `).join('');
    }

    renderSectorHeatmap(sectors) {
        const container = document.getElementById('sector-heatmap');
        if (!container) {
            console.error('❌ sector-heatmap container not found!');
            return;
        }

        console.log(`🎨 Rendering heatmap with ${sectors.length} sectors`);

        container.innerHTML = sectors.map(sector => {
            const sentiment = sector.changePct > 0 ? 'positive' : 
                             sector.changePct < 0 ? 'negative' : 'neutral';
            const opacity = Math.min(Math.abs(sector.changePct) / 3, 1);
            
            return `
                <div class="sector-cell ${sentiment}" 
                     style="opacity: ${0.6 + 0.4 * opacity}; cursor: pointer;"
                     data-symbol="${sector.symbol}"
                     data-sector="${sector.sector}"
                     onclick="window.dashboard.loadSectorStocks('${sector.symbol}', '${sector.sector}')">
                    <div class="sector-name">${sector.sector}</div>
                    <div class="sector-change">
                        ${sector.changePct >= 0 ? '+' : ''}${sector.changePct.toFixed(2)}%
                    </div>
                    <div class="sector-symbol">${sector.symbol}</div>
                </div>
            `;
        }).join('');
        
        console.log('✅ Heatmap rendered successfully');
    }

    async loadSectorStocks(sectorETF, sectorName) {
        console.log(`📊 Loading stocks for sector: ${sectorName} (${sectorETF})`);
        
        const panel = document.getElementById('sector-stocks-panel');
        const titleEl = document.getElementById('sector-stocks-title');
        const container = document.getElementById('sector-stocks-container');
        
        if (!panel || !container) {
            console.error('Sector stocks panel not found');
            return;
        }
        
        // Show panel with loading state
        panel.style.display = 'block';
        titleEl.innerHTML = `📋 ${sectorName} Sector Stocks <span style="color: var(--accent-blue);">(${sectorETF})</span>`;
        container.innerHTML = `
            <div class="loading" style="padding: 40px; text-align: center;">
                <div class="spinner"></div>
                <div style="margin-top: 10px;">Loading ${sectorName} stocks...</div>
            </div>
        `;
        
        // Scroll to panel
        panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
        
        try {
            const response = await fetch(`/api/market/sector/${sectorETF}/stocks`);
            const data = await response.json();
            
            if (data.success && data.stocks && data.stocks.length > 0) {
                this.renderSectorStocks(data.stocks, sectorName, sectorETF);
            } else {
                container.innerHTML = `
                    <div style="padding: 20px; text-align: center; color: var(--text-secondary);">
                        ℹ️ No stocks data available for ${sectorName} sector.
                    </div>
                `;
            }
        } catch (error) {
            console.error('Error loading sector stocks:', error);
            container.innerHTML = `
                <div style="padding: 20px; text-align: center; color: var(--accent-red);">
                    ❌ Error loading sector stocks
                </div>
            `;
        }
    }

    renderSectorStocks(stocks, sectorName, sectorETF) {
        const container = document.getElementById('sector-stocks-container');
        if (!container) return;
        
        // Calculate sector summary
        const avgChange = stocks.reduce((sum, s) => sum + s.changePct, 0) / stocks.length;
        const gainers = stocks.filter(s => s.changePct > 0).length;
        const losers = stocks.filter(s => s.changePct < 0).length;
        
        const html = `
            <div style="margin-bottom: 15px; display: flex; gap: 15px; flex-wrap: wrap;">
                <div style="padding: 10px 15px; background: var(--card-bg); border-radius: 8px; flex: 1; min-width: 120px;">
                    <div style="font-size: 12px; color: var(--text-secondary);">Stocks</div>
                    <div style="font-size: 18px; font-weight: 600;">${stocks.length}</div>
                </div>
                <div style="padding: 10px 15px; background: var(--card-bg); border-radius: 8px; flex: 1; min-width: 120px;">
                    <div style="font-size: 12px; color: var(--text-secondary);">Avg Change</div>
                    <div style="font-size: 18px; font-weight: 600; color: ${avgChange >= 0 ? 'var(--accent-green)' : 'var(--accent-red)'};">
                        ${avgChange >= 0 ? '+' : ''}${avgChange.toFixed(2)}%
                    </div>
                </div>
                <div style="padding: 10px 15px; background: var(--card-bg); border-radius: 8px; flex: 1; min-width: 120px;">
                    <div style="font-size: 12px; color: var(--text-secondary);">Gainers</div>
                    <div style="font-size: 18px; font-weight: 600; color: var(--accent-green);">📈 ${gainers}</div>
                </div>
                <div style="padding: 10px 15px; background: var(--card-bg); border-radius: 8px; flex: 1; min-width: 120px;">
                    <div style="font-size: 12px; color: var(--text-secondary);">Losers</div>
                    <div style="font-size: 18px; font-weight: 600; color: var(--accent-red);">📉 ${losers}</div>
                </div>
            </div>
            <div style="overflow-x: auto;">
                <table class="data-table" style="width: 100%; background: var(--bg-card); border-radius: 8px;">
                    <thead>
                        <tr>
                            <th style="text-align: left; padding: 12px;">Symbol</th>
                            <th style="text-align: left; padding: 12px;">Company</th>
                            <th style="text-align: right; padding: 12px;">Price</th>
                            <th style="text-align: right; padding: 12px;">Change</th>
                            <th style="text-align: right; padding: 12px;">Change %</th>
                            <th style="text-align: right; padding: 12px;">Volume</th>
                            <th style="text-align: right; padding: 12px;">High</th>
                            <th style="text-align: right; padding: 12px;">Low</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${stocks.map(stock => {
                            const changeColor = stock.changePct >= 0 ? 'var(--accent-green)' : 'var(--accent-red)';
                            const arrow = stock.changePct >= 0 ? '▲' : '▼';
                            return `
                                <tr style="border-bottom: 1px solid var(--border-color); cursor: pointer;" 
                                    onclick="window.location.href='/technical-analysis?symbol=${stock.symbol}'">
                                    <td style="padding: 12px;">
                                        <strong style="color: var(--accent-blue); font-size: 14px;">${stock.symbol}</strong>
                                    </td>
                                    <td style="padding: 12px; color: var(--text-secondary); font-size: 12px; max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
                                        ${stock.name}
                                    </td>
                                    <td style="text-align: right; padding: 12px; font-weight: 600;">
                                        $${stock.price.toFixed(2)}
                                    </td>
                                    <td style="text-align: right; padding: 12px; color: ${changeColor}; font-weight: 500;">
                                        ${stock.change >= 0 ? '+' : ''}${stock.change.toFixed(2)}
                                    </td>
                                    <td style="text-align: right; padding: 12px;">
                                        <span style="color: ${changeColor}; font-weight: 600;">
                                            ${arrow} ${stock.changePct >= 0 ? '+' : ''}${stock.changePct.toFixed(2)}%
                                        </span>
                                    </td>
                                    <td style="text-align: right; padding: 12px; color: var(--text-secondary);">
                                        ${this.formatVolume(stock.volume)}
                                    </td>
                                    <td style="text-align: right; padding: 12px; color: var(--text-secondary);">
                                        $${stock.high.toFixed(2)}
                                    </td>
                                    <td style="text-align: right; padding: 12px; color: var(--text-secondary);">
                                        $${stock.low.toFixed(2)}
                                    </td>
                                </tr>
                            `;
                        }).join('')}
                    </tbody>
                </table>
            </div>
        `;
        container.innerHTML = html;
    }

    closeSectorStocks() {
        const panel = document.getElementById('sector-stocks-panel');
        if (panel) {
            panel.style.display = 'none';
        }
    }

    renderMoversTable(movers, direction) {
        const containerId = direction === 'gainers' ? 'gainers-table' : 'losers-table';
        const container = document.getElementById(containerId);
        if (!container) return;

        const tableHTML = `
            <table class="movers-table">
                <thead>
                    <tr>
                        <th>Symbol</th>
                        <th>Price</th>
                        <th>Change</th>
                        <th>Volume</th>
                        <th>High</th>
                        <th>Low</th>
                    </tr>
                </thead>
                <tbody>
                    ${movers.map(stock => `
                        <tr data-symbol="${stock.symbol}">
                            <td class="symbol-cell">${stock.symbol}</td>
                            <td class="price-cell price">$${stock.price.toFixed(2)}</td>
                            <td class="change-cell ${stock.change >= 0 ? 'positive' : 'negative'} change">
                                ${stock.change >= 0 ? '+' : ''}${stock.change.toFixed(2)} 
                                (${stock.changePct >= 0 ? '+' : ''}${stock.changePct.toFixed(2)}%)
                            </td>
                            <td class="volume-cell">${this.formatVolume(stock.volume)}</td>
                            <td class="price-cell">$${stock.high.toFixed(2)}</td>
                            <td class="price-cell">$${stock.low.toFixed(2)}</td>
                        </tr>
                    `).join('')}
                </tbody>
            </table>
        `;

        container.innerHTML = tableHTML;
    }

    renderExtendedHoursAnalysis(data, sessionLabel) {
        const statusContainer = document.getElementById('extended-hours-status');
        const moversContainer = document.getElementById('extended-hours-movers');
        
        if (!statusContainer || !moversContainer) return;
        
        // Update status banner
        const marketStateEmoji = data.marketState === 'PRE_MARKET' ? '🌅' : 
                                 data.marketState === 'AFTER_HOURS' ? '🌆' : '📊';
        
        statusContainer.innerHTML = `
            <div style="display: flex; align-items: center; justify-content: center; gap: 15px;">
                <span style="font-size: 24px;">${marketStateEmoji}</span>
                <div>
                    <div style="font-size: 18px; font-weight: 600; color: var(--text-primary);">
                        ${sessionLabel}
                    </div>
                    <div style="font-size: 13px; color: var(--text-secondary); margin-top: 2px;">
                        ${data.marketDescription} • Scanned ${data.totalScanned} symbols
                    </div>
                </div>
            </div>
        `;
        
        // Show the movers section
        moversContainer.style.display = 'grid';
        
        // Render gainers
        const gainersHTML = this.renderExtendedMoversTable(data.gainers, 'gainers');
        document.getElementById('extended-gainers-table').innerHTML = gainersHTML;
        
        // Render losers
        const losersHTML = this.renderExtendedMoversTable(data.losers, 'losers');
        document.getElementById('extended-losers-table').innerHTML = losersHTML;
        
        console.log(`✅ Extended hours analysis rendered: ${data.gainers.length} gainers, ${data.losers.length} losers`);
    }

    renderExtendedMoversTable(movers, direction) {
        if (!movers || movers.length === 0) {
            return `<div style="text-align: center; padding: 20px; color: var(--text-secondary);">
                No data available
            </div>`;
        }
        
        return `
            <table class="movers-table">
                <thead>
                    <tr>
                        <th>#</th>
                        <th>Symbol</th>
                        <th>Price</th>
                        <th>Change</th>
                        <th>Volume</th>
                    </tr>
                </thead>
                <tbody>
                    ${movers.map((stock, index) => `
                        <tr data-symbol="${stock.symbol}">
                            <td style="text-align: center; color: var(--text-secondary);">${index + 1}</td>
                            <td class="symbol-cell"><strong>${stock.symbol}</strong></td>
                            <td class="price-cell">$${stock.price.toFixed(2)}</td>
                            <td class="change-cell ${stock.change >= 0 ? 'positive' : 'negative'}">
                                ${stock.change >= 0 ? '+' : ''}${stock.change.toFixed(2)} 
                                (${stock.changePct >= 0 ? '+' : ''}${stock.changePct.toFixed(2)}%)
                            </td>
                            <td class="volume-cell" style="font-size: 12px;">
                                ${this.formatVolume(stock.volume)}
                            </td>
                        </tr>
                    `).join('')}
                </tbody>
            </table>
        `;
    }

    renderScannerResults(data, scannerType) {
        const container = document.getElementById('scanner-results');
        const countBadge = document.getElementById('results-count');
        
        if (!container) return;

        let results = [];
        let totalCount = 0;
        
        if (scannerType === 'unified' && data.picks) {
            // Unified scanner returns options, stocks, etfs
            const picks = data.picks;
            const optionsCount = picks.options?.length || 0;
            const stocksCount = picks.stocks?.length || 0;
            const etfsCount = picks.etfs?.length || 0;
            totalCount = optionsCount + stocksCount + etfsCount;
            
            if (totalCount === 0) {
                container.innerHTML = this.renderEmptyState('Scanning completed. Waiting for results...');
                if (countBadge) countBadge.textContent = '0 Results';
                return;
            }
            
            if (countBadge) {
                countBadge.textContent = `${totalCount} Total (📊 ${optionsCount} Options, 📈 ${stocksCount} Stocks, 🏦 ${etfsCount} ETFs)`;
            }
            
            // Render by category
            let html = '';
            
            if (picks.options && picks.options.length > 0) {
                html += '<h3 style="margin: 20px 0 15px 0; color: var(--accent-blue);">💰 OPTIONS PICKS</h3>';
                html += this.renderOptionsTable(picks.options);
            }
            
            if (picks.stocks && picks.stocks.length > 0) {
                html += '<h3 style="margin: 30px 0 15px 0; color: var(--accent-green);">📈 STOCK PICKS</h3>';
                html += this.renderScannerTable(picks.stocks, 'unified-stocks');
            }
            
            if (picks.etfs && picks.etfs.length > 0) {
                html += '<h3 style="margin: 30px 0 15px 0; color: var(--accent-purple);">📊 ETF PICKS</h3>';
                html += this.renderScannerTable(picks.etfs, 'unified-etfs');
            }
            
            container.innerHTML = html;
            
        } else if (scannerType === 'short-squeeze' && data.candidates) {
            results = data.candidates;
            totalCount = results.length;
            if (countBadge) countBadge.textContent = `${totalCount} Candidates`;
            
            if (results.length === 0) {
                container.innerHTML = this.renderEmptyState('No short squeeze candidates found');
                return;
            }
            
            container.innerHTML = this.renderScannerTable(results, 'short-squeeze');
            
        } else if (scannerType === 'quality-stocks' && data.stocks) {
            results = data.stocks;
            totalCount = results.length;
            if (countBadge) countBadge.textContent = `${totalCount} Quality Stocks`;
            
            if (results.length === 0) {
                container.innerHTML = this.renderEmptyState('No quality stocks found');
                return;
            }
            
            container.innerHTML = this.renderScannerTable(results, 'quality-stocks');
            
        } else if (scannerType === 'weekly-screener' && data.stocks) {
            results = data.stocks;
            totalCount = results.length;
            if (countBadge) countBadge.textContent = `${totalCount} Weekly Picks`;
            
            if (results.length === 0) {
                container.innerHTML = this.renderEmptyState('No weekly picks found');
                return;
            }
            
            container.innerHTML = this.renderScannerTable(results, 'weekly-screener');
            
        } else if (scannerType === 'golden-cross' && data.stocks) {
            results = data.stocks;
            totalCount = results.length;
            if (countBadge) countBadge.textContent = `${totalCount} Golden Cross Signals`;
            
            if (results.length === 0) {
                container.innerHTML = this.renderEmptyState('No golden cross signals found');
                return;
            }
            
            // Render golden cross as a table
            container.innerHTML = this.renderGoldenCrossTable(results);
            
        } else if (scannerType === 'triple-confirmation-all' && data) {
            // Combined Triple Confirmation - show all three timeframes
            const swingResults = data.swing || [];
            const intradayResults = data.intraday || [];
            const positionalResults = data.positional || [];
            totalCount = swingResults.length + intradayResults.length + positionalResults.length;
            
            if (countBadge) {
                countBadge.textContent = `${totalCount} Total (📊 ${swingResults.length} Swing, ⚡ ${intradayResults.length} Intraday, 📈 ${positionalResults.length} Positional)`;
            }
            
            if (totalCount === 0) {
                container.innerHTML = this.renderEmptyState('No triple confirmation signals found across all timeframes');
                return;
            }
            
            // Render all three sections
            let html = '';
            
            if (swingResults.length > 0) {
                html += '<h3 style="margin: 20px 0 15px 0; color: var(--accent-blue);">📊 SWING TRADES (Multi-Day Holds)</h3>';
                html += this.renderScannerTable(swingResults, 'triple-confirmation');
            }
            
            if (intradayResults.length > 0) {
                html += '<h3 style="margin: 30px 0 15px 0; color: var(--accent-green);">⚡ INTRADAY (Same-Day Trades)</h3>';
                html += this.renderScannerTable(intradayResults, 'triple-intraday');
            }
            
            if (positionalResults.length > 0) {
                html += '<h3 style="margin: 30px 0 15px 0; color: var(--accent-purple);">📈 POSITIONAL (Long-Term Holds)</h3>';
                html += this.renderScannerTable(positionalResults, 'triple-positional');
            }
            
            container.innerHTML = html;
            
        } else if (scannerType === 'triple-confirmation' && data.stocks) {
            results = data.stocks;
            totalCount = results.length;
            if (countBadge) countBadge.textContent = `${totalCount} Triple Confirmation (Swing)`;
            
            if (results.length === 0) {
                container.innerHTML = this.renderEmptyState('No triple confirmation signals found');
                return;
            }
            
            container.innerHTML = this.renderScannerTable(results, 'triple-confirmation');
            
        } else if (scannerType === 'triple-intraday' && data.stocks) {
            results = data.stocks;
            totalCount = results.length;
            if (countBadge) countBadge.textContent = `${totalCount} Triple Confirmation (Intraday)`;
            
            if (results.length === 0) {
                container.innerHTML = this.renderEmptyState('No intraday signals found');
                return;
            }
            
            container.innerHTML = this.renderScannerTable(results, 'triple-intraday');
            
        } else if (scannerType === 'triple-positional' && data.stocks) {
            results = data.stocks;
            totalCount = results.length;
            if (countBadge) countBadge.textContent = `${totalCount} Triple Confirmation (Positional)`;
            
            if (results.length === 0) {
                container.innerHTML = this.renderEmptyState('No positional signals found');
                return;
            }
            
            container.innerHTML = this.renderScannerTable(results, 'triple-positional');
            
        } else if (scannerType === 'etf-scanner' && data.stocks) {
            results = data.stocks;
            totalCount = results.length;
            if (countBadge) countBadge.textContent = `${totalCount} ETFs Scanned`;
            
            if (results.length === 0) {
                container.innerHTML = this.renderEmptyState('No ETF results found');
                return;
            }
            
            container.innerHTML = this.renderETFTable(results);
            
        } else if (scannerType === 'custom-analyzer' && data) {
            // Custom analyzer - data is passed directly as stocks array
            results = data;
            totalCount = results.length;
            if (countBadge) countBadge.textContent = `${totalCount} Custom Analysis Results`;
            
            if (results.length === 0) {
                container.innerHTML = this.renderEmptyState('No analysis results available');
                return;
            }
            
            container.innerHTML = this.renderPicksCards(results);
            
        } else {
            container.innerHTML = this.renderEmptyState('No results available');
            if (countBadge) countBadge.textContent = '0 Results';
        }
    }
    
    renderPicksCards(items) {
        return items.map(item => {
            // Check if this is an error result (delisted stock)
            if (item.error && item.error_message) {
                return `
                <div class="scanner-card fade-in" style="border-left: 3px solid var(--accent-red); background: rgba(255, 0, 0, 0.05);">
                    <div class="scanner-header">
                        <div class="scanner-symbol" style="color: var(--accent-red);">${item.symbol || 'UNKNOWN'}</div>
                        <div style="color: var(--accent-red); font-size: 12px; font-weight: 600;">⚠️ ERROR</div>
                    </div>
                    <div style="padding: 15px; background: rgba(0,0,0,0.2); border-radius: 6px; margin-top: 10px;">
                        <div style="font-size: 13px; color: var(--text-primary); line-height: 1.6;">
                            ${item.error_message}
                        </div>
                    </div>
                </div>
                `;
            }
            
            // Check if this is a custom analyzer result with support/resistance
            const hasLevels = item.support_levels && item.resistance_levels && 
                              Array.isArray(item.support_levels) && Array.isArray(item.resistance_levels) &&
                              item.support_levels.length > 0 && item.resistance_levels.length > 0;
            
            return `
            <div class="scanner-card fade-in">
                <div class="scanner-header">
                    <div class="scanner-symbol">${item.symbol || item.ticker}</div>
                    <div class="scanner-score">${item.score || item.total_score || 0}/15</div>
                </div>
                <div class="scanner-details">
                    <div class="detail-item">
                        <div class="detail-label">Price</div>
                        <div class="detail-value">$${(item.price || item.current_price || item.data?.price || 0).toFixed(2)}</div>
                    </div>
                    <div class="detail-item">
                        <div class="detail-label">RSI</div>
                        <div class="detail-value">${(item.rsi || item.data?.rsi || 0).toFixed(1)}</div>
                    </div>
                    <div class="detail-item">
                        <div class="detail-label">Volume</div>
                        <div class="detail-value">${this.formatVolume(item.volume || item.data?.volume || 0)}</div>
                    </div>
                </div>
                ${hasLevels ? `
                    <div style="margin-top: 15px; padding-top: 15px; border-top: 1px solid var(--border-color);">
                        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 15px; margin-bottom: 10px;">
                            <div>
                                <div style="font-size: 11px; color: var(--text-secondary); margin-bottom: 5px;">🛡️ SUPPORT LEVELS</div>
                                ${item.support_levels && item.support_levels.length > 0 ? `
                                    <div style="font-size: 10px; line-height: 1.6;">
                                        ${item.support_levels.length >= 3 ? `<div><span style="color: var(--text-secondary);">S1:</span> <span style="color: var(--accent-green); font-weight: 600;">$${item.support_levels[2].toFixed(2)}</span> <span style="font-size: 9px; color: var(--text-secondary);">(nearest)</span></div>` : ''}
                                        ${item.support_levels.length >= 2 ? `<div><span style="color: var(--text-secondary);">S2:</span> <span style="color: var(--accent-green);">$${item.support_levels[1].toFixed(2)}</span></div>` : ''}
                                        ${item.support_levels.length >= 1 ? `<div><span style="color: var(--text-secondary);">S3:</span> <span style="color: var(--accent-green);">$${item.support_levels[0].toFixed(2)}</span> <span style="font-size: 9px; color: var(--text-secondary);">(furthest)</span></div>` : ''}
                                    </div>
                                ` : '<div style="font-size: 12px; color: var(--text-secondary);">N/A</div>'}
                            </div>
                            <div>
                                <div style="font-size: 11px; color: var(--text-secondary); margin-bottom: 5px;">🚧 RESISTANCE LEVELS</div>
                                ${item.resistance_levels && item.resistance_levels.length > 0 ? `
                                    <div style="font-size: 10px; line-height: 1.6;">
                                        ${item.resistance_levels.length >= 3 ? `<div><span style="color: var(--text-secondary);">R1:</span> <span style="color: var(--accent-red); font-weight: 600;">$${item.resistance_levels[2].toFixed(2)}</span> <span style="font-size: 9px; color: var(--text-secondary);">(nearest)</span></div>` : ''}
                                        ${item.resistance_levels.length >= 2 ? `<div><span style="color: var(--text-secondary);">R2:</span> <span style="color: var(--accent-red);">$${item.resistance_levels[1].toFixed(2)}</span></div>` : ''}
                                        ${item.resistance_levels.length >= 1 ? `<div><span style="color: var(--text-secondary);">R3:</span> <span style="color: var(--accent-red);">$${item.resistance_levels[0].toFixed(2)}</span> <span style="font-size: 9px; color: var(--text-secondary);">(furthest)</span></div>` : ''}
                                    </div>
                                ` : '<div style="font-size: 12px; color: var(--text-secondary);">N/A</div>'}
                            </div>
                        </div>
                    </div>
                ` : ''}
                ${item.recommendation ? `
                    <div style="margin-top: 15px; padding: 12px; background: ${item.recommendation === 'BUY' ? 'rgba(0, 255, 0, 0.1)' : item.recommendation === 'SELL' ? 'rgba(255, 0, 0, 0.1)' : 'rgba(128, 128, 128, 0.1)'}; border-radius: 6px; border-left: 3px solid ${item.recommendation === 'BUY' ? 'var(--accent-green)' : item.recommendation === 'SELL' ? 'var(--accent-red)' : '#999'};">
                        <div style="font-size: 13px; font-weight: 700; color: ${item.recommendation === 'BUY' ? 'var(--accent-green)' : item.recommendation === 'SELL' ? 'var(--accent-red)' : '#999'}; margin-bottom: 8px;">
                            ${item.recommendation === 'BUY' ? '📈 BUY SIGNAL' : item.recommendation === 'SELL' ? '📉 SELL SIGNAL' : '⏸️ HOLD'}
                        </div>
                        <div style="font-size: 10px; color: var(--text-secondary); margin-bottom: 10px;">
                            ${item.recommendation_reason}
                        </div>
                        ${item.recommendation !== 'HOLD' ? `
                            <div style="display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px; font-size: 10px;">
                                <div>
                                    <div style="color: var(--text-secondary); margin-bottom: 2px;">Entry Price</div>
                                    <div style="font-weight: 600; color: #fff;">$${item.entry_price?.toFixed(2)}</div>
                                </div>
                                <div>
                                    <div style="color: var(--text-secondary); margin-bottom: 2px;">Stop Loss ${item.recommendation === 'SELL' ? '(↑ above)' : '(↓ below)'}</div>
                                    <div style="font-weight: 600; color: var(--accent-red);">$${item.stop_loss?.toFixed(2)}</div>
                                </div>
                                ${item.target_1 ? `
                                    <div>
                                        <div style="color: var(--text-secondary); margin-bottom: 2px;">Target 1 ${item.recommendation === 'BUY' ? '(R1)' : '(S1)'}</div>
                                        <div style="font-weight: 600; color: var(--accent-green);">$${item.target_1.toFixed(2)}</div>
                                    </div>
                                ` : ''}
                                ${item.target_2 ? `
                                    <div>
                                        <div style="color: var(--text-secondary); margin-bottom: 2px;">Target 2 ${item.recommendation === 'BUY' ? '(R2)' : '(S2)'}</div>
                                        <div style="font-weight: 600; color: var(--accent-green);">$${item.target_2.toFixed(2)}</div>
                                    </div>
                                ` : ''}
                                ${item.target_3 ? `
                                    <div style="grid-column: 1 / -1;">
                                        <div style="color: var(--text-secondary); margin-bottom: 2px;">Target 3 ${item.recommendation === 'BUY' ? '(R3)' : '(S3)'}</div>
                                        <div style="font-weight: 600; color: var(--accent-green);">$${item.target_3.toFixed(2)} ${item.risk_reward_1 ? `<span style="color: var(--text-secondary); font-size: 9px;">(R:R ${item.risk_reward_1}:1)</span>` : ''}</div>
                                    </div>
                                ` : ''}
                            </div>
                        ` : ''}
                    </div>
                ` : ''}
                ${item.signals && item.signals.length > 0 ? `
                    <div class="signals-list">
                        ${item.signals.slice(0, 4).map(signal => 
                            `<span class="signal-badge">${signal}</span>`
                        ).join('')}
                    </div>
                ` : ''}
                ${item.recommendation && item.recommendation !== 'HOLD' ? `
                    <button class="btn btn-primary" style="width: 100%; margin-top: 15px; padding: 10px; font-size: 13px;" 
                            onclick="addToMonitor('${item.symbol || item.ticker}', 
                                                   '${item.type || 'STOCK'}', 
                                                   '${item.recommendation}' === 'BUY' ? 'LONG' : 'SHORT', 
                                                   ${item.entry_price}, 
                                                   ${item.stop_loss}, 
                                                   ${item.target_3 || item.target_2 || item.target_1})">
                        👁️ Add to Monitor
                    </button>
                ` : ''}
            </div>
        `;
        }).join('');
    }

    renderScannerTable(stocks, scannerType) {
        // Check if this is a triple confirmation scanner (uses different data fields)
        const isTripleConfirmation = scannerType && (
            scannerType.includes('triple') || 
            (stocks.length > 0 && stocks[0].volume_ratio !== undefined && stocks[0].distance_from_vwap !== undefined)
        );
        
        // Check if this is quality stocks scanner
        const isQualityStocks = scannerType === 'quality-stocks' || 
            (stocks.length > 0 && stocks[0].quality_score !== undefined);
        
        return `
            <div style="overflow-x: auto;">
                <table class="data-table" style="width: 100%; background: var(--bg-card); border-radius: 8px;">
                    <thead>
                        <tr>
                            <th style="text-align: left; padding: 12px;">Symbol</th>
                            <th style="text-align: left; padding: 12px;">Direction</th>
                            <th style="text-align: center; padding: 12px;">Score</th>
                            <th style="text-align: right; padding: 12px;">Price</th>
                            <th style="text-align: right; padding: 12px;">${isQualityStocks ? 'Qual Score' : isTripleConfirmation ? 'Vol Ratio' : 'RSI'}</th>
                            <th style="text-align: right; padding: 12px;">${isQualityStocks ? 'Growth 🚀' : isTripleConfirmation ? 'VWAP Dist' : 'Volume'}</th>
                            <th style="text-align: right; padding: 12px;">${isQualityStocks ? 'Drawdown' : 'Entry'}</th>
                            <th style="text-align: right; padding: 12px;">${isQualityStocks ? 'Mkt Cap' : 'Stop Loss'}</th>
                            <th style="text-align: right; padding: 12px;">${isQualityStocks ? 'Volatility' : 'Target'}</th>
                            <th style="text-align: center; padding: 12px;">${isQualityStocks ? 'Sector' : 'R:R'}</th>
                            <th style="text-align: center; padding: 12px;">Action</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${stocks.map(stock => {
                            const score = stock.score || stock.total_score || stock.squeeze_score || stock.opportunity_score || stock.quality_score || 0;
                            const price = stock.price || stock.current_price || 0;
                            const direction = stock.direction || 'BULLISH';
                            
                            // Triple Confirmation specific fields
                            const volumeRatio = stock.volume_ratio || 1.0;
                            const vwapDistance = stock.distance_from_vwap || 0;
                            
                            // Standard scanner fields
                            const rsi = stock.rsi || (stock.data && stock.data.rsi) || 0;
                            const volume = stock.volume || stock.avg_volume || (stock.data && stock.data.volume) || 0;
                            
                            const entryPrice = stock.entry_price || price;
                            const stopLoss = stock.stop_loss || (entryPrice * 0.95);
                            const target = stock.target || stock.target_price || stock.target_1 || (entryPrice * 1.15);
                            const riskReward = stock.risk_reward || stock.rr_ratio || ((target - entryPrice) / Math.abs(entryPrice - stopLoss));
                            
                            // Different color thresholds for quality stocks (0-100) vs others (0-15)
                            const scoreColor = isQualityStocks 
                                ? (score >= 70 ? 'var(--accent-green)' : score >= 50 ? '#ffa500' : score >= 30 ? '#ffcc00' : 'var(--text-secondary)')
                                : (score >= 12 ? 'var(--accent-green)' : score >= 9 ? '#ffa500' : score >= 6 ? '#ffcc00' : 'var(--text-secondary)');
                            
                            const company = stock.company || stock.company_name || stock.name || stock.symbol || stock.ticker || '';
                            const sector = stock.sector || '';
                            const symbol = stock.symbol || stock.ticker || '';
                            const directionColor = direction === 'BULLISH' ? 'var(--accent-green)' : direction === 'BEARISH' ? 'var(--accent-red)' : direction === 'NEUTRAL' ? '#ffa500' : 'var(--text-secondary)';
                            
                            return `
                                <tr style="border-bottom: 1px solid var(--border-color);">
                                    <td style="padding: 12px;">
                                        <strong style="color: var(--accent-blue); font-size: 14px;">${symbol}</strong>
                                    </td>
                                    <td style="padding: 12px;">
                                        <div style="display: inline-block; padding: 3px 8px; background: ${directionColor}22; border-radius: 8px; font-weight: 600; color: ${directionColor}; font-size: 11px;">
                                            ${direction === 'BULLISH' ? '📈 BULL' : direction === 'BEARISH' ? '📉 BEAR' : direction === 'NEUTRAL' ? '➡️ NEUTRAL' : direction}
                                        </div>
                                    </td>
                                    <td style="text-align: center; padding: 12px;">
                                        <div style="display: inline-block; padding: 4px 10px; background: ${scoreColor}33; border-radius: 12px; font-weight: 600; color: ${scoreColor};">
                                            ${isQualityStocks ? `${score.toFixed(0)}/100` : `${score.toFixed(0)}/15`}
                                        </div>
                                    </td>
                                    <td style="text-align: right; padding: 12px; font-weight: 600;">
                                        $${price.toFixed(2)}
                                    </td>
                                    <td style="text-align: right; padding: 12px;">
                                        ${isQualityStocks ? `
                                            <span style="color: ${(stock.quality_score || 0) >= 70 ? 'var(--accent-green)' : (stock.quality_score || 0) >= 50 ? '#ffa500' : 'var(--text-secondary)'}; font-weight: 600;">
                                                ${(stock.quality_score || 0).toFixed(0)}/100
                                            </span>
                                        ` : isTripleConfirmation ? `
                                            <span style="color: ${volumeRatio >= 1.5 ? 'var(--accent-green)' : volumeRatio >= 1.2 ? '#ffa500' : 'var(--text-secondary)'}; font-weight: 600;">
                                                ${volumeRatio.toFixed(2)}x
                                            </span>
                                        ` : `
                                            <span style="color: ${rsi < 30 ? 'var(--accent-green)' : rsi > 70 ? 'var(--accent-red)' : 'var(--text-secondary)'}; font-weight: 600;">
                                                ${rsi.toFixed(1)}
                                            </span>
                                        `}
                                    </td>
                                    <td style="text-align: right; padding: 12px; font-size: 11px;">
                                        ${isQualityStocks ? `
                                            <span style="color: ${(stock.growth_potential || 0) >= 50 ? 'var(--accent-green)' : (stock.growth_potential || 0) >= 30 ? '#ffa500' : 'var(--text-secondary)'}; font-weight: 600;">
                                                ${(stock.growth_potential || 0).toFixed(0)}
                                            </span>
                                        ` : isTripleConfirmation ? `
                                            <span style="color: ${Math.abs(vwapDistance) < 1 ? 'var(--accent-green)' : Math.abs(vwapDistance) < 3 ? '#ffa500' : 'var(--text-secondary)'};">
                                                ${vwapDistance >= 0 ? '+' : ''}${vwapDistance.toFixed(2)}%
                                            </span>
                                        ` : `
                                            <span style="color: var(--text-secondary);">
                                                ${this.formatVolume(volume)}
                                            </span>
                                        `}
                                    </td>
                                    <td style="text-align: right; padding: 12px; ${isQualityStocks ? 'color: var(--accent-red)' : 'color: var(--accent-blue)'}; font-weight: 600;">
                                        ${isQualityStocks ? `${(stock.drawdown_from_high || 0).toFixed(1)}%` : `$${entryPrice.toFixed(2)}`}
                                    </td>
                                    <td style="text-align: right; padding: 12px; ${isQualityStocks ? 'font-size: 11px; color: var(--text-secondary)' : 'color: var(--accent-red)'};">
                                        ${isQualityStocks ? `$${((stock.market_cap || 0) / 1e9).toFixed(2)}B` : `$${stopLoss.toFixed(2)}`}
                                    </td>
                                    <td style="text-align: right; padding: 12px; ${isQualityStocks ? 'color: #ffa500; font-weight: 600' : 'color: var(--accent-green)'};">
                                        ${isQualityStocks ? `${(stock.volatility || 0).toFixed(0)}%` : `$${target.toFixed(2)}`}
                                    </td>
                                    <td style="text-align: center; padding: 12px;">
                                        ${isQualityStocks ? `
                                            <span style="font-size: 11px; color: var(--text-secondary);">${sector}</span>
                                        ` : `
                                            <span style="color: ${riskReward >= 3 ? 'var(--accent-green)' : '#ffa500'}; font-weight: 600;">1:${riskReward.toFixed(1)}</span>
                                        `}
                                    </td>
                                    <td style="text-align: center; padding: 12px;">
                                        <button class="btn btn-primary" style="padding: 6px 12px; font-size: 12px; white-space: nowrap;"
                                                onclick="addToMonitor('${symbol}', 'STOCK', 'LONG', ${entryPrice}, ${stopLoss}, ${target})">
                                            👁️ Monitor
                                        </button>
                                    </td>
                                </tr>
                            `;
                        }).join('')}
                    </tbody>
                </table>
            </div>
        `;
    }

    renderOptionsTable(options) {
        return `
            <div style="overflow-x: auto;">
                <table class="data-table" style="width: 100%; background: var(--bg-card); border-radius: 8px;">
                    <thead>
                        <tr>
                            <th style="text-align: left; padding: 12px;">Symbol</th>
                            <th style="text-align: center; padding: 12px;">Signal</th>
                            <th style="text-align: center; padding: 12px;">Score</th>
                            <th style="text-align: right; padding: 12px;">Stock Price</th>
                            <th style="text-align: right; padding: 12px;">Strike</th>
                            <th style="text-align: left; padding: 12px;">Expiry</th>
                            <th style="text-align: right; padding: 12px;">Entry Premium</th>
                            <th style="text-align: right; padding: 12px;">Stop Loss</th>
                            <th style="text-align: right; padding: 12px;">Target</th>
                            <th style="text-align: center; padding: 12px;">R:R</th>
                            <th style="text-align: center; padding: 12px;">Action</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${options.map(opt => {
                            const score = opt.score || opt.total_score || 0;
                            const stockPrice = opt.price || opt.current_price || 0;
                            const strike = opt.strike_price || 0;
                            const signal = opt.signal || opt.direction || 'CALL';
                            const entryPremium = opt.entry_premium || 0;
                            const stopLoss = opt.stop_loss_premium || (entryPremium * 0.5);
                            const target = opt.target_premium_5x || opt.target_premium || (entryPremium * 3);
                            const expiry = opt.expiry_date || opt.expiry || '';
                            const riskReward = ((target - entryPremium) / (entryPremium - stopLoss));
                            const scoreColor = score >= 12 ? 'var(--accent-green)' : score >= 9 ? '#ffa500' : score >= 6 ? '#ffcc00' : 'var(--text-secondary)';
                            const symbol = opt.symbol || opt.ticker || '';
                            const signalColor = signal === 'CALL' ? 'var(--accent-green)' : 'var(--accent-red)';
                            
                            return `
                                <tr style="border-bottom: 1px solid var(--border-color);">
                                    <td style="padding: 12px;">
                                        <strong style="color: var(--accent-blue); font-size: 14px;">${symbol}</strong>
                                    </td>
                                    <td style="text-align: center; padding: 12px;">
                                        <div style="display: inline-block; padding: 4px 10px; background: ${signalColor}33; border-radius: 12px; font-weight: 600; color: ${signalColor};">
                                            ${signal}
                                        </div>
                                    </td>
                                    <td style="text-align: center; padding: 12px;">
                                        <div style="display: inline-block; padding: 4px 10px; background: ${scoreColor}33; border-radius: 12px; font-weight: 600; color: ${scoreColor};">
                                            ${score.toFixed(0)}/15
                                        </div>
                                    </td>
                                    <td style="text-align: right; padding: 12px; font-weight: 600;">
                                        $${stockPrice.toFixed(2)}
                                    </td>
                                    <td style="text-align: right; padding: 12px; color: var(--text-secondary);">
                                        $${strike.toFixed(2)}
                                    </td>
                                    <td style="text-align: left; padding: 12px; color: var(--text-secondary); font-size: 11px;">
                                        ${expiry}
                                    </td>
                                    <td style="text-align: right; padding: 12px; color: var(--accent-blue); font-weight: 600;">
                                        $${entryPremium.toFixed(2)}
                                    </td>
                                    <td style="text-align: right; padding: 12px; color: var(--accent-red);">
                                        $${stopLoss.toFixed(2)}
                                    </td>
                                    <td style="text-align: right; padding: 12px; color: var(--accent-green);">
                                        $${target.toFixed(2)}
                                    </td>
                                    <td style="text-align: center; padding: 12px;">
                                        <span style="color: ${riskReward >= 3 ? 'var(--accent-green)' : '#ffa500'}; font-weight: 600;">1:${riskReward.toFixed(1)}</span>
                                    </td>
                                    <td style="text-align: center; padding: 12px;">
                                        <button class="btn btn-primary" style="padding: 6px 12px; font-size: 12px; white-space: nowrap;"
                                                onclick="addToMonitor('${symbol}', 'OPTION', '${signal}', ${entryPremium}, ${stopLoss}, ${target}, ${strike}, '${expiry}')">
                                            👁️ Monitor
                                        </button>
                                    </td>
                                </tr>
                            `;
                        }).join('')}
                    </tbody>
                </table>
            </div>
        `;
    }

    renderGoldenCrossTable(stocks) {
        return `
            <div style="overflow-x: auto;">
                <table class="data-table" style="width: 100%; background: var(--bg-card); border-radius: 8px;">
                    <thead>
                        <tr>
                            <th style="text-align: left; padding: 12px;">Symbol</th>
                            <th style="text-align: left; padding: 12px;">Company</th>
                            <th style="text-align: center; padding: 12px;">Score</th>
                            <th style="text-align: right; padding: 12px;">Price</th>
                            <th style="text-align: right; padding: 12px;">RSI</th>
                            <th style="text-align: right; padding: 12px;">Entry</th>
                            <th style="text-align: right; padding: 12px;">Stop Loss</th>
                            <th style="text-align: right; padding: 12px;">Target</th>
                            <th style="text-align: center; padding: 12px;">R:R</th>
                            <th style="text-align: center; padding: 12px;">Action</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${stocks.map(stock => {
                            const riskReward = stock.risk_reward || 0;
                            const scoreColor = stock.score >= 12 ? 'var(--accent-green)' : stock.score >= 9 ? '#ffa500' : 'var(--text-secondary)';
                            return `
                                <tr style="border-bottom: 1px solid var(--border-color);">
                                    <td style="padding: 12px;">
                                        <strong style="color: var(--accent-blue); font-size: 14px;">${stock.symbol}</strong>
                                    </td>
                                    <td style="padding: 12px;">
                                        <div style="font-size: 12px; color: var(--text-primary);">${stock.company || stock.symbol}</div>
                                        <div style="font-size: 10px; color: var(--text-secondary); margin-top: 2px;">${stock.sector || ''}</div>
                                    </td>
                                    <td style="text-align: center; padding: 12px;">
                                        <div style="display: inline-block; padding: 4px 10px; background: ${scoreColor}33; border-radius: 12px; font-weight: 600; color: ${scoreColor};">
                                            ${stock.score}/15
                                        </div>
                                    </td>
                                    <td style="text-align: right; padding: 12px; font-weight: 600;">
                                        $${stock.price.toFixed(2)}
                                    </td>
                                    <td style="text-align: right; padding: 12px;">
                                        <span style="color: ${stock.rsi < 30 ? 'var(--accent-green)' : stock.rsi > 70 ? 'var(--accent-red)' : 'var(--text-secondary)'};">
                                            ${stock.rsi.toFixed(1)}
                                        </span>
                                    </td>
                                    <td style="text-align: right; padding: 12px; color: var(--accent-blue); font-weight: 600;">
                                        $${stock.entry_price.toFixed(2)}
                                    </td>
                                    <td style="text-align: right; padding: 12px; color: var(--accent-red);">
                                        $${stock.stop_loss.toFixed(2)}
                                    </td>
                                    <td style="text-align: right; padding: 12px; color: var(--accent-green);">
                                        $${stock.target.toFixed(2)}
                                    </td>
                                    <td style="text-align: center; padding: 12px;">
                                        <span style="color: var(--accent-green); font-weight: 600;">1:${riskReward.toFixed(1)}</span>
                                    </td>
                                    <td style="text-align: center; padding: 12px;">
                                        <button class="btn btn-primary" style="padding: 6px 12px; font-size: 12px; white-space: nowrap;"
                                                onclick="addToMonitor('${stock.symbol}', 'STOCK', 'LONG', ${stock.entry_price}, ${stock.stop_loss}, ${stock.target})">
                                            👁️ Monitor
                                        </button>
                                    </td>
                                </tr>
                            `;
                        }).join('')}
                    </tbody>
                </table>
            </div>
        `;
    }

    renderETFTable(stocks) {
        // Group ETFs by category
        const categories = {};
        stocks.forEach(stock => {
            const cat = stock.category || 'Other';
            if (!categories[cat]) categories[cat] = [];
            categories[cat].push(stock);
        });
        
        let html = '';
        
        for (const [category, etfs] of Object.entries(categories)) {
            const categoryIcon = category.includes('Crypto') ? '🪙' : 
                                 category.includes('Commodity') ? '🛢️' : 
                                 category.includes('Sector') ? '📊' : '📈';
            
            html += '<h3 style="margin: 20px 0 15px 0; color: var(--accent-blue); font-size: 16px;">' + categoryIcon + ' ' + category + '</h3>';
            html += '<div style="overflow-x: auto; margin-bottom: 20px;">';
            html += '<table class="data-table" style="width: 100%; background: var(--bg-card); border-radius: 8px;">';
            html += '<thead><tr>';
            html += '<th style="text-align: left; padding: 12px;">Symbol</th>';
            html += '<th style="text-align: left; padding: 12px;">Name</th>';
            html += '<th style="text-align: center; padding: 12px;">Score</th>';
            html += '<th style="text-align: center; padding: 12px;">Direction</th>';
            html += '<th style="text-align: right; padding: 12px;">Price</th>';
            html += '<th style="text-align: right; padding: 12px;">Change %</th>';
            html += '<th style="text-align: right; padding: 12px;">RSI</th>';
            html += '<th style="text-align: right; padding: 12px;">Volume</th>';
            html += '<th style="text-align: right; padding: 12px;">52W High</th>';
            html += '<th style="text-align: right; padding: 12px;">52W Low</th>';
            html += '</tr></thead><tbody>';
            
            etfs.forEach(stock => {
                const scoreColor = stock.score >= 10 ? 'var(--accent-green)' : stock.score >= 6 ? '#ffa500' : 'var(--text-secondary)';
                const dirColor = stock.direction === 'BULLISH' ? 'var(--accent-green)' : stock.direction === 'BEARISH' ? 'var(--accent-red)' : 'var(--text-secondary)';
                const changeColor = stock.change_pct >= 0 ? 'var(--accent-green)' : 'var(--accent-red)';
                const rsiColor = stock.rsi < 30 ? 'var(--accent-green)' : stock.rsi > 70 ? 'var(--accent-red)' : 'var(--text-secondary)';
                const displayName = stock.name ? stock.name.replace(/\s*\([^)]*\)\s*$/, '') : stock.symbol;
                
                html += '<tr style="border-bottom: 1px solid var(--border-color);">';
                html += '<td style="padding: 12px;"><strong style="color: var(--accent-blue); font-size: 14px;">' + stock.symbol + '</strong></td>';
                html += '<td style="padding: 12px;"><div style="font-size: 12px; color: var(--text-primary);">' + displayName + '</div></td>';
                html += '<td style="text-align: center; padding: 12px;"><div style="display: inline-block; padding: 4px 10px; background: ' + scoreColor + '33; border-radius: 12px; font-weight: 600; color: ' + scoreColor + ';">' + stock.score + '/15</div></td>';
                html += '<td style="text-align: center; padding: 12px;"><span style="padding: 4px 8px; border-radius: 12px; font-size: 11px; font-weight: 600; background: ' + dirColor + '22; color: ' + dirColor + ';">' + stock.direction + '</span></td>';
                html += '<td style="text-align: right; padding: 12px; font-weight: 600;">$' + stock.price.toFixed(2) + '</td>';
                html += '<td style="text-align: right; padding: 12px; color: ' + changeColor + '; font-weight: 600;">' + (stock.change_pct >= 0 ? '+' : '') + stock.change_pct.toFixed(2) + '%</td>';
                html += '<td style="text-align: right; padding: 12px;"><span style="color: ' + rsiColor + ';">' + (stock.rsi ? stock.rsi.toFixed(1) : 'N/A') + '</span></td>';
                html += '<td style="text-align: right; padding: 12px; font-size: 11px;">' + (stock.volume_ratio ? stock.volume_ratio.toFixed(1) + 'x' : 'N/A') + '</td>';
                html += '<td style="text-align: right; padding: 12px; font-size: 11px;">$' + (stock.high_52w ? stock.high_52w.toFixed(2) : 'N/A') + ' <span style="font-size: 10px; color: ' + (stock.pct_from_high < 5 ? 'var(--accent-green)' : 'var(--text-secondary)') + ';">(' + (stock.pct_from_high ? stock.pct_from_high.toFixed(1) : 0) + '% away)</span></td>';
                html += '<td style="text-align: right; padding: 12px; font-size: 11px;">$' + (stock.low_52w ? stock.low_52w.toFixed(2) : 'N/A') + ' <span style="font-size: 10px; color: ' + (stock.pct_from_low < 10 ? 'var(--accent-red)' : 'var(--text-secondary)') + ';">(+' + (stock.pct_from_low ? stock.pct_from_low.toFixed(1) : 0) + '%)</span></td>';
                html += '</tr>';
            });
            
            html += '</tbody></table></div>';
        }
        
        return html;
    }

    renderActivePositions(positions, stats = null) {
        const container = document.getElementById('positions-list');
        const totalPnlEl = document.getElementById('total-pnl');
        const activeCountEl = document.getElementById('active-count');
        const winRateEl = document.getElementById('win-rate');
        
        if (!container) return;

        if (!positions || positions.length === 0) {
            container.innerHTML = this.renderEmptyState('No active positions. Add positions from Scanners or Options page.');
            if (totalPnlEl) totalPnlEl.textContent = '$0.00';
            if (activeCountEl) activeCountEl.textContent = '0';
            if (winRateEl) winRateEl.textContent = '0%';
            return;
        }

        // Calculate total P&L (direction-aware)
        let totalPnl = 0;
        positions.forEach(pos => {
            const isOption = pos.type === 'OPTION';
            const multiplier = isOption ? 100 : 1;  // Options: 1 contract = 100 shares
            const isShort = pos.direction === 'SHORT';
            const pnl = isShort
                ? (pos.entry_price - pos.current_price) * pos.quantity * multiplier
                : (pos.current_price - pos.entry_price) * pos.quantity * multiplier;
            totalPnl += pnl;
        });
        
        // Update summary stats
        if (totalPnlEl) {
            totalPnlEl.textContent = `${totalPnl >= 0 ? '+' : ''}$${totalPnl.toFixed(2)}`;
            totalPnlEl.style.color = totalPnl >= 0 ? 'var(--accent-green)' : 'var(--accent-red)';
        }
        if (activeCountEl) {
            activeCountEl.textContent = positions.length;
        }
        if (winRateEl && stats) {
            const winRate = stats.total_trades > 0 ? (stats.winning_trades / stats.total_trades * 100) : 0;
            winRateEl.textContent = `${winRate.toFixed(1)}%`;
            winRateEl.style.color = winRate >= 50 ? 'var(--accent-green)' : 'var(--accent-red)';
            
            // Update win/loss text
            const winLossEl = document.getElementById('win-loss-text');
            if (winLossEl) {
                winLossEl.textContent = `${stats.winning_trades}W / ${stats.losing_trades}L`;
            }
        }

        container.innerHTML = `
            <div style="overflow-x: auto;">
                <table style="width: 100%; border-collapse: collapse; font-size: 13px;">
                    <thead>
                        <tr style="background: var(--card-bg); border-bottom: 2px solid var(--border-color);">
                            <th style="padding: 12px 8px; text-align: left; color: var(--text-secondary); font-weight: 600;">Symbol</th>
                            <th style="padding: 12px 8px; text-align: left; color: var(--text-secondary); font-weight: 600;">Type</th>
                            <th style="padding: 12px 8px; text-align: right; color: var(--text-secondary); font-weight: 600;">Entry</th>
                            <th style="padding: 12px 8px; text-align: right; color: var(--text-secondary); font-weight: 600;">Current</th>
                            <th style="padding: 12px 8px; text-align: center; color: var(--text-secondary); font-weight: 600;">Qty</th>
                            <th style="padding: 12px 8px; text-align: right; color: var(--text-secondary); font-weight: 600;">P&L</th>
                            <th style="padding: 12px 8px; text-align: right; color: var(--text-secondary); font-weight: 600;">%</th>
                            <th style="padding: 12px 8px; text-align: right; color: var(--text-secondary); font-weight: 600;">Stop Loss</th>
                            <th style="padding: 12px 8px; text-align: right; color: var(--text-secondary); font-weight: 600;">Target</th>
                            <th style="padding: 12px 8px; text-align: left; color: var(--text-secondary); font-weight: 600;">Updated</th>
                            <th style="padding: 12px 8px; text-align: left; color: var(--text-secondary); font-weight: 600;">Opened</th>
                            <th style="padding: 12px 8px; text-align: left; color: var(--text-secondary); font-weight: 600;">Status</th>
                            <th style="padding: 12px 8px; text-align: center; color: var(--text-secondary); font-weight: 600;">Actions</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${positions.map(pos => {
                            const isOption = pos.type === 'OPTION';
                            const multiplier = isOption ? 100 : 1;
                            const isShort = pos.direction === 'SHORT';
                            const pnl = isShort
                                ? (pos.entry_price - pos.current_price) * pos.quantity * multiplier
                                : (pos.current_price - pos.entry_price) * pos.quantity * multiplier;
                            const pnlPct = isShort
                                ? ((pos.entry_price - pos.current_price) / pos.entry_price) * 100
                                : ((pos.current_price - pos.entry_price) / pos.entry_price) * 100;
                            const isProfit = pnl >= 0;
                            
                            // Check alert conditions (direction-aware)
                            const stopLossHit = isShort
                                ? pos.current_price >= pos.stop_loss   // SHORT: SL hit when price rises above stop
                                : pos.current_price <= pos.stop_loss;  // LONG: SL hit when price drops below stop
                            const targetHit = isShort
                                ? pos.current_price <= (pos.target_1 || pos.target)   // SHORT: target hit when price drops below target
                                : pos.current_price >= (pos.target_1 || pos.target);  // LONG: target hit when price rises above target
                            
                            let statusIcon = '📊';
                            let statusText = 'Active';
                            let statusColor = 'var(--accent-blue)';
                            
                            if (stopLossHit) {
                                statusIcon = '🚨';
                                statusText = 'Stop Loss Hit';
                                statusColor = 'var(--accent-red)';
                            } else if (targetHit) {
                                statusIcon = '🎯';
                                statusText = 'Target Reached';
                                statusColor = 'var(--accent-green)';
                            }
                            
                            return `
                                <tr style="border-bottom: 1px solid var(--border-color); transition: background 0.2s;" 
                                    onmouseover="this.style.background='rgba(74, 158, 255, 0.05)'" 
                                    onmouseout="this.style.background='transparent'">
                                    <td style="padding: 10px 8px;">
                                        <div style="display:flex;align-items:center;gap:8px;">
                                            <div style="font-weight:700;color:var(--text-primary);font-size:14px;">${pos.symbol}</div>
                                            <div style="font-size:10px;padding:2px 6px;border-radius:8px;background:${pos.source === 'bot' ? '#fff4e6' : '#e6fff0'};color:${pos.source === 'bot' ? '#b76200' : '#0a8a3a'};font-weight:700;">
                                                ${pos.source ? (pos.source === 'bot' ? 'BOT' : 'MANUAL') : 'MANUAL'}
                                            </div>
                                        </div>
                                        ${isOption ? `
                                            <div style="font-size: 10px; color: var(--text-secondary); margin-top: 2px;">
                                                Strike: $${pos.strike?.toFixed(0)}
                                            </div>
                                            <div style="font-size: 10px; color: var(--accent-orange); font-weight: 600; margin-top: 2px;">
                                                📅 Exp: ${pos.expiration || 'N/A'}
                                            </div>
                                        ` : ''}
                                    </td>
                                    <td style="padding: 10px 8px;">
                                        <span class="signal-badge ${pos.direction === 'CALL' ? 'bullish' : pos.direction === 'PUT' ? 'bearish' : ''}" style="font-size: 10px; padding: 3px 6px;">
                                            ${pos.direction || pos.type}
                                        </span>
                                    </td>
                                    <td style="padding: 10px 8px; text-align: right; color: var(--text-primary);">
                                        $${pos.entry_price.toFixed(2)}
                                    </td>
                                    <td style="padding: 10px 8px; text-align: right; color: ${isProfit ? 'var(--accent-green)' : 'var(--accent-red)'}; font-weight: 700; font-size: 14px;">
                                        <div>$${pos.current_price.toFixed(2)}</div>
                                        ${isOption && pos.premium_source === 'LIVE' ? `
                                            <div style="font-size: 8px; background: var(--accent-green); color: #000; display: inline-block; padding: 1px 4px; border-radius: 2px; margin-top: 2px; font-weight: 600;">
                                                🔴 LIVE
                                            </div>
                                        ` : isOption ? `
                                            <div style="font-size: 8px; color: var(--text-secondary); margin-top: 2px;">
                                                (entry)
                                            </div>
                                        ` : ''}
                                    </td>
                                    <td style="padding: 10px 8px; text-align: center; color: var(--text-primary);">
                                        ${pos.quantity} ${isOption ? 'c' : 's'}
                                    </td>
                                    <td style="padding: 10px 8px; text-align: right; color: ${isProfit ? 'var(--accent-green)' : 'var(--accent-red)'}; font-weight: 700; font-size: 14px;">
                                        ${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}
                                    </td>
                                    <td style="padding: 10px 8px; text-align: right; color: ${pnlPct >= 0 ? 'var(--accent-green)' : 'var(--accent-red)'}; font-weight: 600;">
                                        ${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(1)}%
                                    </td>
                                    <td style="padding: 10px 8px; text-align: right; color: var(--accent-red); font-weight: 600;">
                                        $${pos.stop_loss.toFixed(2)}
                                        ${stopLossHit ? ' 🚨' : ''}
                                    </td>
                                    <td style="padding: 10px 8px; text-align: right;">
                                        ${isOption ? `
                                            <div style="font-size: 11px; line-height: 1.4;">
                                                <div style="color: var(--accent-green);">${pos.target_1 ? '$' + pos.target_1.toFixed(2) : '-'} ${isShort ? (pos.current_price <= (pos.target_1 || 0) ? '✓' : '') : (pos.current_price >= (pos.target_1 || 0) ? '✓' : '')}</div>
                                                <div style="color: var(--accent-green); font-weight: 600;">${pos.target_2 ? '$' + pos.target_2.toFixed(2) : '-'} ${isShort ? (pos.current_price <= (pos.target_2 || 0) ? '✓' : '') : (pos.current_price >= (pos.target_2 || 0) ? '✓' : '')}</div>
                                                <div style="color: var(--accent-green); font-weight: 700;">${pos.target_3 ? '$' + pos.target_3.toFixed(2) : '-'} ${isShort ? (pos.current_price <= (pos.target_3 || 0) ? '✓' : '') : (pos.current_price >= (pos.target_3 || 0) ? '✓' : '')}</div>
                                            </div>
                                        ` : `
                                            <div style="color: var(--accent-green); font-weight: 600;">
                                                $${pos.target.toFixed(2)}
                                                ${targetHit ? ' 🎯' : ''}
                                            </div>
                                        `}
                                    </td>
                                    <td style="padding: 10px 8px; font-size: 11px; color: var(--text-secondary); white-space: nowrap;">
                                        ${(() => {
                                            const ts = pos.last_checked || pos.last_price_update;
                                            if (!ts) return '--';
                                            const t = new Date(ts);
                                            if (isNaN(t.getTime())) return '--';
                                            const ageSec = Math.floor((Date.now() - t.getTime()) / 1000);
                                            let ageStr;
                                            if (ageSec < 0 || ageSec > 86400) { ageStr = t.toLocaleTimeString(); }
                                            else if (ageSec < 60) { ageStr = ageSec + 's ago'; }
                                            else if (ageSec < 3600) { ageStr = Math.floor(ageSec / 60) + 'm ago'; }
                                            else { ageStr = Math.floor(ageSec / 3600) + 'h ago'; }
                                            const pStatus = pos.price_update_status || '';
                                            const color = pStatus === 'updated' ? '#10b981' : pStatus === 'stale' ? '#f59e0b' : '#6b7280';
                                            const dot = pStatus === 'updated' ? '●' : '○';
                                            return '<span style="color:' + color + ';">' + dot + '</span> ' + ageStr;
                                        })()}
                                    </td>
                                    <td style="padding: 10px 8px; font-size: 11px; color: var(--text-secondary);">
                                        ${pos.date_added ? new Date(pos.date_added).toLocaleDateString('en-US', { day: 'numeric', month: 'short' }) + '<br>' + new Date(pos.date_added).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' }) : 'N/A'}
                                    </td>
                                    <td style="padding: 10px 8px; text-align: center;">
                                        <div style="display: flex; align-items: center; gap: 5px; font-size: 11px; color: ${statusColor}; font-weight: 600;">
                                            <span>${statusIcon}</span>
                                            <span style="white-space: nowrap;">${statusText}</span>
                                        </div>
                                    </td>
                                    <td style="padding: 10px 8px;">
                                        <div style="display: flex; gap: 4px; flex-wrap: wrap; justify-content: center;">
                                            <button class="btn btn-primary" style="font-size: 10px; padding: 4px 8px; white-space: nowrap;" 
                                                    onclick="closePositionAt('${pos.position_key}', ${pos.stop_loss}, 'Stop Loss')"
                                                    title="Close at Stop Loss">
                                                🛑 SL
                                            </button>
                                            ${isOption ? `
                                                <button class="btn btn-primary" style="font-size: 10px; padding: 4px 8px; white-space: nowrap;" 
                                                        onclick="closePositionAt('${pos.position_key}', ${pos.target_1 || pos.target}, 'Target 1:2')"
                                                        title="Close at Target 1:2">
                                                    🎯 T1
                                                </button>
                                                <button class="btn btn-primary" style="font-size: 10px; padding: 4px 8px; white-space: nowrap;" 
                                                        onclick="closePositionAt('${pos.position_key}', ${pos.target_2 || pos.target}, 'Target 1:3')"
                                                        title="Close at Target 1:3">
                                                    🎯 T2
                                                </button>
                                                <button class="btn btn-primary" style="font-size: 10px; padding: 4px 8px; white-space: nowrap;" 
                                                        onclick="closePositionAt('${pos.position_key}', ${pos.target_3 || pos.target}, 'Target 1:4')"
                                                        title="Close at Target 1:4">
                                                    🎯 T3
                                                </button>
                                            ` : `
                                                <button class="btn btn-primary" style="font-size: 10px; padding: 4px 8px; white-space: nowrap;" 
                                                        onclick="closePositionAt('${pos.position_key}', ${pos.target}, 'Target')"
                                                        title="Close at Target">
                                                    🎯 Target
                                                </button>
                                            `}
                                            <button class="btn btn-primary" style="font-size: 10px; padding: 4px 8px; white-space: nowrap;" 
                                                    onclick="closePositionCustom('${pos.position_key}', ${pos.current_price})"
                                                    title="Close at Custom Price">
                                                💰 Custom
                                            </button>
                                            <button class="btn" style="font-size: 10px; padding: 4px 8px; background: var(--accent-red); border-color: var(--accent-red); white-space: nowrap;" 
                                                    onclick="deletePosition('${pos.position_key}')"
                                                    title="Delete Position">
                                                🗑️
                                            </button>
                                        </div>
                                    </td>
                                </tr>
                            `;
                        }).join('')}
                    </tbody>
                </table>
            </div>
        `;
    }

    renderClosedPositions(closedPositions, stats) {
        const container = document.getElementById('closed-positions-list');
        const countBadge = document.getElementById('closed-count');
        
        if (!container) return;

        if (countBadge) {
            countBadge.textContent = `${closedPositions?.length || 0} Closed`;
        }

        if (!closedPositions || closedPositions.length === 0) {
            container.innerHTML = this.renderEmptyState('No closed positions yet');
            return;
        }

        // Sort by date closed (most recent first)
        const sorted = [...closedPositions].sort((a, b) => 
            new Date(b.date_closed) - new Date(a.date_closed)
        );

        // Calculate total P&L
        let totalPnl = 0;
        let totalWins = 0;
        let totalLosses = 0;
        closedPositions.forEach(pos => {
            totalPnl += pos.pnl;
            if (pos.pnl > 0) totalWins++;
            else if (pos.pnl < 0) totalLosses++;
        });
        const winRate = closedPositions.length > 0 ? ((totalWins / closedPositions.length) * 100).toFixed(1) : 0;

        container.innerHTML = `
            <div style="overflow-x: auto;">
                <table style="width: 100%; border-collapse: collapse; font-size: 13px;">
                    <thead>
                        <tr style="background: var(--card-bg); border-bottom: 2px solid var(--border-color);">
                            <th style="padding: 12px 8px; text-align: left; color: var(--text-secondary); font-weight: 600;">Symbol</th>
                            <th style="padding: 12px 8px; text-align: left; color: var(--text-secondary); font-weight: 600;">Type</th>
                            <th style="padding: 12px 8px; text-align: right; color: var(--text-secondary); font-weight: 600;">Entry</th>
                            <th style="padding: 12px 8px; text-align: right; color: var(--text-secondary); font-weight: 600;">Exit</th>
                            <th style="padding: 12px 8px; text-align: center; color: var(--text-secondary); font-weight: 600;">Qty</th>
                            <th style="padding: 12px 8px; text-align: right; color: var(--text-secondary); font-weight: 600;">P&L</th>
                            <th style="padding: 12px 8px; text-align: right; color: var(--text-secondary); font-weight: 600;">%</th>
                            <th style="padding: 12px 8px; text-align: left; color: var(--text-secondary); font-weight: 600;">Reason</th>
                            <th style="padding: 12px 8px; text-align: left; color: var(--text-secondary); font-weight: 600;">Opened</th>
                            <th style="padding: 12px 8px; text-align: left; color: var(--text-secondary); font-weight: 600;">Closed</th>
                            <th style="padding: 12px 8px; text-align: center; color: var(--text-secondary); font-weight: 600;">Action</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${sorted.map(pos => {
                            const pnl = pos.pnl;
                            const pnlPct = pos.pnl_pct;
                            const isOption = pos.type === 'OPTION';
                            const isWin = pnl > 0;
                            
                            return `
                                <tr style="border-bottom: 1px solid var(--border-color); transition: background 0.2s;" 
                                    onmouseover="this.style.background='rgba(74, 158, 255, 0.05)'" 
                                    onmouseout="this.style.background='transparent'">
                                    <td style="padding: 10px 8px;">
                                        <div style="font-weight: 600; color: var(--text-primary);">${pos.symbol}</div>
                                        ${isOption ? `<div style="font-size: 11px; color: var(--text-secondary);">$${pos.strike?.toFixed(0)} Strike</div>` : ''}
                                    </td>
                                    <td style="padding: 10px 8px;">
                                        <span class="signal-badge ${pos.direction === 'CALL' ? 'bullish' : pos.direction === 'PUT' ? 'bearish' : ''}" style="font-size: 10px; padding: 3px 6px;">
                                            ${pos.direction}
                                        </span>
                                    </td>
                                    <td style="padding: 10px 8px; text-align: right; color: var(--text-primary);">$${pos.entry_price.toFixed(2)}</td>
                                    <td style="padding: 10px 8px; text-align: right; color: ${isWin ? 'var(--accent-green)' : 'var(--accent-red)'}; font-weight: 600;">
                                        $${pos.exit_price.toFixed(2)}
                                    </td>
                                    <td style="padding: 10px 8px; text-align: center; color: var(--text-primary);">
                                        ${pos.quantity} ${isOption ? 'c' : 's'}
                                    </td>
                                    <td style="padding: 10px 8px; text-align: right; color: ${isWin ? 'var(--accent-green)' : 'var(--accent-red)'}; font-weight: 700;">
                                        ${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}
                                    </td>
                                    <td style="padding: 10px 8px; text-align: right; color: ${pnlPct >= 0 ? 'var(--accent-green)' : 'var(--accent-red)'}; font-weight: 600;">
                                        ${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(1)}%
                                    </td>
                                    <td style="padding: 10px 8px; font-size: 11px; color: var(--text-secondary);">${pos.close_reason}</td>
                                    <td style="padding: 10px 8px; font-size: 11px; color: var(--text-secondary);">
                                        ${pos.date_added ? new Date(pos.date_added).toLocaleDateString('en-US', { day: 'numeric', month: 'short', year: 'numeric' }) + ', ' + new Date(pos.date_added).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' }) : 'N/A'}
                                    </td>
                                    <td style="padding: 10px 8px; font-size: 11px; color: var(--text-secondary);">
                                        ${pos.date_closed ? new Date(pos.date_closed).toLocaleDateString('en-US', { day: 'numeric', month: 'short', year: 'numeric' }) + ', ' + new Date(pos.date_closed).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' }) : 'N/A'}
                                    </td>
                                    <td style="padding: 10px 8px; text-align: center;">
                                        <button class="btn" style="font-size: 10px; padding: 4px 8px; background: #555; border-color: #555;" 
                                                onclick="deletePosition('${pos.position_key}')">
                                            🗑️
                                        </button>
                                    </td>
                                </tr>
                            `;
                        }).join('')}
                    </tbody>
                    <tfoot>
                        <tr style="background: var(--card-bg); border-top: 2px solid var(--border-color); font-weight: 700;">
                            <td colspan="5" style="padding: 14px 8px; text-align: right; color: var(--text-primary); font-size: 14px;">
                                💰 TOTAL PROFIT/LOSS:
                            </td>
                            <td style="padding: 14px 8px; text-align: right; color: ${totalPnl >= 0 ? 'var(--accent-green)' : 'var(--accent-red)'}; font-size: 16px; font-weight: 800;">
                                ${totalPnl >= 0 ? '+' : ''}$${totalPnl.toFixed(2)}
                            </td>
                            <td colspan="2" style="padding: 14px 8px; text-align: left; color: var(--text-secondary); font-size: 12px;">
                                <div>✅ ${totalWins} Wins | ❌ ${totalLosses} Losses</div>
                                <div style="margin-top: 4px;">📊 Win Rate: ${winRate}%</div>
                            </td>
                            <td colspan="3" style="padding: 14px 8px;"></td>
                        </tr>
                    </tfoot>
                </table>
            </div>
        `;
    }

    renderPerformanceStats(activePositions, closedPositions, stats) {
        // Calculate unrealized P&L from active positions
        let unrealizedPnl = 0;
        if (activePositions && activePositions.length > 0) {
            activePositions.forEach(pos => {
                const isOption = pos.type === 'OPTION';
                const multiplier = isOption ? 100 : 1;
                const pnl = (pos.current_price - pos.entry_price) * pos.quantity * multiplier;
                unrealizedPnl += pnl;
            });
        }
        
        // Realized P&L from stats (closed positions)
        const realizedPnl = stats?.closed_pnl || 0;
        const combinedPnl = realizedPnl + unrealizedPnl;
        
        // Update P&L Summary elements
        const realizedEl = document.getElementById('realized-pnl');
        const unrealizedEl = document.getElementById('unrealized-pnl');
        const combinedEl = document.getElementById('combined-pnl');
        
        if (realizedEl) {
            realizedEl.textContent = `${realizedPnl >= 0 ? '+' : ''}$${realizedPnl.toFixed(2)}`;
            realizedEl.className = realizedPnl >= 0 ? 'stat-positive' : 'stat-negative';
            realizedEl.style.color = realizedPnl >= 0 ? 'var(--accent-green)' : 'var(--accent-red)';
        }
        if (unrealizedEl) {
            unrealizedEl.textContent = `${unrealizedPnl >= 0 ? '+' : ''}$${unrealizedPnl.toFixed(2)}`;
            unrealizedEl.className = unrealizedPnl >= 0 ? 'stat-positive' : 'stat-negative';
            unrealizedEl.style.color = unrealizedPnl >= 0 ? 'var(--accent-green)' : 'var(--accent-red)';
        }
        if (combinedEl) {
            combinedEl.textContent = `${combinedPnl >= 0 ? '+' : ''}$${combinedPnl.toFixed(2)}`;
            combinedEl.style.color = combinedPnl >= 0 ? 'var(--accent-green)' : 'var(--accent-red)';
        }
        
        // Update Trade Statistics elements
        const totalTradesEl = document.getElementById('total-trades');
        const winningTradesEl = document.getElementById('winning-trades');
        const losingTradesEl = document.getElementById('losing-trades');
        
        if (totalTradesEl) totalTradesEl.textContent = stats?.total_trades || 0;
        if (winningTradesEl) winningTradesEl.textContent = stats?.winning_trades || 0;
        if (losingTradesEl) losingTradesEl.textContent = stats?.losing_trades || 0;
        
        // Update Performance Metrics elements
        const avgWinEl = document.getElementById('avg-win');
        const avgLossEl = document.getElementById('avg-loss');
        const profitFactorEl = document.getElementById('profit-factor');
        const largestWinEl = document.getElementById('largest-win');
        
        if (avgWinEl) avgWinEl.textContent = `$${(stats?.avg_win || 0).toFixed(2)}`;
        if (avgLossEl) avgLossEl.textContent = `-$${(stats?.avg_loss || 0).toFixed(2)}`;
        if (profitFactorEl) profitFactorEl.textContent = (stats?.profit_factor || 0).toFixed(2);
        if (largestWinEl) largestWinEl.textContent = `$${(stats?.largest_win || 0).toFixed(2)}`;
        
        console.log('📊 Performance stats updated:', { realizedPnl, unrealizedPnl, combinedPnl, stats });
    }

    // ========================================================================
    // UTILITIES
    // ========================================================================

    formatVolume(volume) {
        if (volume >= 1000000) {
            return `${(volume / 1000000).toFixed(2)}M`;
        } else if (volume >= 1000) {
            return `${(volume / 1000).toFixed(2)}K`;
        }
        return volume.toString();
    }

    showLoading(containerId) {
        const container = document.getElementById(containerId);
        if (container) {
            container.innerHTML = `
                <div class="loading">
                    <div class="spinner"></div>
                    <div>Loading data...</div>
                </div>
            `;
        }
    }

    showError(message) {
        console.error(message);
        // Could show a toast notification here
    }

    renderEmptyState(message) {
        return `
            <div class="empty-state">
                <div class="empty-state-icon">📊</div>
                <div>${message}</div>
            </div>
        `;
    }

    setupEventListeners() {
        // Refresh buttons
        document.querySelectorAll('.refresh-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const target = e.target.dataset.refresh;
                this.refreshData(target);
            });
        });

        // Tab switches
        document.querySelectorAll('[data-tab]').forEach(tab => {
            tab.addEventListener('click', (e) => {
                this.switchTab(e.target.dataset.tab);
            });
        });
    }

    refreshData(target) {
        switch(target) {
            case 'market':
                this.loadMarketOverview();
                break;
            case 'sectors':
                this.loadSectorPerformance();
                break;
            case 'gainers':
                this.loadTopMovers('gainers');
                break;
            case 'losers':
                this.loadTopMovers('losers');
                break;
            case 'extended':
                this.loadExtendedHoursAnalysis();
                break;
            case 'volume-spike':
                this.loadVolumeSpikeScanner();
                break;
            default:
                console.log('Unknown refresh target:', target);
        }
    }

    startAutoRefresh() {
        // Refresh market data every 2 minutes using batch API (reduced to save API quota)
        this.refreshInterval = setInterval(() => {
            console.log('🔄 Auto-refresh: reloading dashboard data...');
            this.loadDashboardBatch();
        }, 120000);  // 2 minutes
    }
    
    // ========================================================================
    // Market Pulse - Live advancing/declining counts
    // ========================================================================
    updateMarketPulse(pulseData) {
        const advEl = document.getElementById('advancing-count');
        const decEl = document.getElementById('declining-count');
        const sentEl = document.getElementById('market-sentiment');
        const sentDetail = document.getElementById('sentiment-detail');
        const advPctEl = document.getElementById('advancing-pct');
        const decPctEl = document.getElementById('declining-pct');
        const adRatioEl = document.getElementById('ad-ratio');
        const adRatioLabel = document.getElementById('ad-ratio-label');
        const pulseSource = document.getElementById('pulse-source');
        
        if (!pulseData || !advEl || !decEl) return;
        
        const advancing = pulseData.advancing || 0;
        const declining = pulseData.declining || 0;
        const total = pulseData.total || (advancing + declining);
        
        // Update counts
        advEl.textContent = advancing;
        decEl.textContent = declining;
        
        // Update percentages
        if (advPctEl && total > 0) {
            advPctEl.textContent = `${((advancing / total) * 100).toFixed(0)}% of ${total} stocks`;
        }
        if (decPctEl && total > 0) {
            decPctEl.textContent = `${((declining / total) * 100).toFixed(0)}% of ${total} stocks`;
        }

        // Update A/D ratio
        if (adRatioEl) {
            const adRatio = pulseData.advance_decline_ratio || (declining > 0 ? (advancing / declining).toFixed(2) : advancing);
            adRatioEl.textContent = adRatio;
            adRatioEl.style.color = adRatio >= 1 ? '#10b981' : '#ef4444';
        }
        if (adRatioLabel && total > 0) {
            adRatioLabel.textContent = `${total} stocks tracked`;
        }

        // Update source label
        if (pulseSource) {
            pulseSource.textContent = pulseData.source || `${total} stocks`;
        }
        
        // Calculate sentiment based on ratio
        if (sentEl && sentDetail && total > 0) {
            const ratio = advancing / total;
            if (ratio > 0.65) {
                sentEl.textContent = '🟢 Strong Bullish';
                sentEl.style.color = '#10b981';
                sentDetail.textContent = 'Broad market strength';
            } else if (ratio > 0.52) {
                sentEl.textContent = '🟢 Bullish';
                sentEl.style.color = '#10b981';
                sentDetail.textContent = 'More stocks advancing';
            } else if (ratio >= 0.48) {
                sentEl.textContent = '🟡 Neutral';
                sentEl.style.color = '#f59e0b';
                sentDetail.textContent = 'Mixed signals';
            } else if (ratio >= 0.35) {
                sentEl.textContent = '🔴 Bearish';
                sentEl.style.color = '#ef4444';
                sentDetail.textContent = 'More stocks declining';
            } else {
                sentEl.textContent = '🔴 Strong Bearish';
                sentEl.style.color = '#ef4444';
                sentDetail.textContent = 'Broad market weakness';
            }
        }
        
        // Update timestamp
        const lastUpdateEl = document.getElementById('last-update');
        const updateStatusEl = document.getElementById('update-status');
        if (lastUpdateEl) {
            lastUpdateEl.textContent = new Date().toLocaleTimeString();
        }
        if (updateStatusEl) {
            updateStatusEl.textContent = 'Live';
            updateStatusEl.style.color = '#10b981';
        }
        
        console.log(`📊 Market Pulse updated: ${advancing} advancing, ${declining} declining`);
    }
    
    // ========================================================================
    // Market Closed Banner - Scrolling notification for holidays/weekends/closed
    // ========================================================================
    updateMarketClosedBanner(marketStatus) {
        const banner = document.getElementById('market-closed-banner');
        const msgEl = document.getElementById('banner-message');
        const msgDupEl = document.getElementById('banner-message-dup');
        
        if (!banner) return;
        
        // Check if user dismissed the banner this session
        if (sessionStorage.getItem('bannerDismissed') === 'true') {
            banner.classList.remove('active');
            return;
        }
        
        const status = marketStatus.status;
        const text = marketStatus.text || '';
        
        // Only show banner when market is CLOSED (not pre-market or after-hours)
        if (status === 'CLOSED') {
            banner.classList.add('active');
            
            // Set appropriate banner style based on reason
            banner.classList.remove('holiday', 'weekend');
            
            let message = 'Market is currently closed';
            
            if (text.toLowerCase().includes('holiday')) {
                banner.classList.add('holiday');
                message = `🎉 ${text} - Markets are closed today`;
            } else if (text.toLowerCase().includes('weekend')) {
                banner.classList.add('weekend');
                message = '📅 Weekend - Markets are closed';
            } else {
                message = `⏸️ ${text}`;
            }
            
            if (msgEl) msgEl.textContent = message;
            if (msgDupEl) msgDupEl.textContent = message;
            
            console.log(`🔔 Market closed banner shown: ${text}`);
        } else {
            banner.classList.remove('active');
            // Clear dismissal when market opens so banner can show again next close
            sessionStorage.removeItem('bannerDismissed');
        }
    }

    stopAutoRefresh() {
        if (this.refreshInterval) {
            clearInterval(this.refreshInterval);
        }
    }

    switchTab(tabName) {
        console.log('Switching to tab:', tabName);
        // Implement tab switching logic
    }
}

// ============================================================================
// INITIALIZE ON PAGE LOAD
// ============================================================================

// NOTE: Global dashboard initialization moved to individual pages
// This allows each page to control its own initialization behavior
// - index.html (dashboard): new TradingDashboard() - full init
// - monitoring.html: new TradingDashboard(false) - minimal init
// - scanners.html/options.html: can add their own initialization as needed
