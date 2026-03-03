// Symbol autocomplete for technical_analysis.html and custom search pages
// Requires: input#symbol-input or input.symbol-search-input

document.addEventListener('DOMContentLoaded', function() {
    // Find all symbol input fields (support multiple on same page)
    const inputs = document.querySelectorAll('#symbol-input, .symbol-search-input');
    
    inputs.forEach(input => {
        if (!input) return;
        
        // Create suggestion box
        const suggestionBox = document.createElement('div');
        suggestionBox.className = 'symbol-suggestion-box';
        suggestionBox.style.cssText = `
            position: absolute;
            background: var(--bg-secondary, #1a1a2e);
            border: 1px solid var(--border-color, #333);
            border-radius: 8px;
            z-index: 9999;
            display: none;
            max-height: 280px;
            overflow-y: auto;
            min-width: 300px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.3);
        `;
        
        // Wrap input in relative container if not already
        if (input.parentNode.style.position !== 'relative') {
            input.parentNode.style.position = 'relative';
        }
        input.parentNode.appendChild(suggestionBox);

        let suggestions = [];
        let selectedIndex = -1;
        let debounceTimer = null;

        input.addEventListener('input', function(e) {
            clearTimeout(debounceTimer);
            const query = input.value.trim();
            
            if (query.length < 2) {
                suggestionBox.style.display = 'none';
                return;
            }
            
            // Debounce API calls
            debounceTimer = setTimeout(async () => {
                try {
                    const resp = await fetch(`/api/symbol/suggest?q=${encodeURIComponent(query)}`);
                    const data = await resp.json();
                    if (data.success && data.suggestions.length > 0) {
                        suggestions = data.suggestions;
                        selectedIndex = -1;
                        renderSuggestions(suggestions, query);
                    } else {
                        suggestionBox.style.display = 'none';
                    }
                } catch (err) {
                    console.error('Symbol suggest error:', err);
                    suggestionBox.style.display = 'none';
                }
            }, 200);
        });

        // Keyboard navigation
        input.addEventListener('keydown', function(e) {
            if (suggestionBox.style.display === 'none') return;
            
            if (e.key === 'ArrowDown') {
                e.preventDefault();
                selectedIndex = Math.min(selectedIndex + 1, suggestions.length - 1);
                highlightSelection();
            } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                selectedIndex = Math.max(selectedIndex - 1, 0);
                highlightSelection();
            } else if (e.key === 'Enter' && selectedIndex >= 0) {
                e.preventDefault();
                selectSuggestion(suggestions[selectedIndex]);
            } else if (e.key === 'Escape') {
                suggestionBox.style.display = 'none';
            }
        });

        function highlightSelection() {
            const items = suggestionBox.querySelectorAll('.suggestion-item');
            items.forEach((item, idx) => {
                if (idx === selectedIndex) {
                    item.style.background = 'var(--accent-blue, #3b82f6)';
                    item.style.color = 'white';
                    item.scrollIntoView({ block: 'nearest' });
                } else {
                    item.style.background = 'transparent';
                    item.style.color = 'var(--text-primary, #fff)';
                }
            });
        }

        function selectSuggestion(s) {
            input.value = s.symbol;
            suggestionBox.style.display = 'none';
            input.dispatchEvent(new Event('change'));
            // Trigger analysis if function exists
            if (typeof analyzeSymbol === 'function') {
                analyzeSymbol();
            }
        }

        function renderSuggestions(suggestions, query) {
            suggestionBox.innerHTML = '';
            suggestions.forEach((s, idx) => {
                const item = document.createElement('div');
                item.className = 'suggestion-item';
                item.style.cssText = `
                    padding: 10px 14px;
                    cursor: pointer;
                    border-bottom: 1px solid var(--border-color, #333);
                    display: flex;
                    justify-content: space-between;
                    align-items: center;
                    transition: background 0.15s;
                `;
                
                // Highlight matching text
                const nameHtml = highlightMatch(s.name, query);
                const symbolHtml = highlightMatch(s.symbol, query);
                
                item.innerHTML = `
                    <div>
                        <span style="font-weight: 600; color: var(--accent-green, #10b981); font-size: 14px;">${symbolHtml}</span>
                        <span style="color: var(--text-secondary, #888); font-size: 13px; margin-left: 8px;">${nameHtml}</span>
                    </div>
                    <span style="color: var(--text-muted, #666); font-size: 11px;">${s.exchange || ''}</span>
                `;
                
                item.addEventListener('mouseenter', () => {
                    selectedIndex = idx;
                    highlightSelection();
                });
                
                item.addEventListener('mousedown', function(e) {
                    e.preventDefault();
                    selectSuggestion(s);
                });
                
                suggestionBox.appendChild(item);
            });
            
            // Position below input
            suggestionBox.style.left = '0';
            suggestionBox.style.top = (input.offsetHeight + 4) + 'px';
            suggestionBox.style.width = Math.max(input.offsetWidth, 300) + 'px';
            suggestionBox.style.display = 'block';
        }

        function highlightMatch(text, query) {
            if (!text || !query) return text || '';
            const regex = new RegExp(`(${query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi');
            return text.replace(regex, '<mark style="background: var(--accent-yellow, #fbbf24); color: #000; padding: 0 2px; border-radius: 2px;">$1</mark>');
        }

        // Hide on blur
        input.addEventListener('blur', function() {
            setTimeout(() => suggestionBox.style.display = 'none', 200);
        });
        
        // Show on focus if has value
        input.addEventListener('focus', function() {
            if (input.value.trim().length >= 2 && suggestions.length > 0) {
                suggestionBox.style.display = 'block';
            }
        });
    });
});
