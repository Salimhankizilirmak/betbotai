const API_BASE_URL = ''; // Relative paths for unified serving
window.allMatches = []; // Global storage for search functionality

document.addEventListener('DOMContentLoaded', () => {
    const dateEl = document.getElementById('current-date');
    if (dateEl) {
        dateEl.innerText = new Date().toLocaleDateString('tr-TR', {
            weekday: 'long', year: 'numeric', month: 'long', day: 'numeric'
        });
    }

    fetchMatches(true);
    fetchBetHistory(); 
    fetchPlayerProps(); 
    fetchCoupons();
    fetchLogs();
    setInterval(fetchLogs, 2000);

    document.getElementById('refresh-btn').addEventListener('click', () => {
        const mg = document.getElementById('matches-grid');
        if(mg) mg.innerHTML = '<div class="loading-spinner"><i class="fa-solid fa-circle-notch fa-spin"></i> Canlı ve gelecek veriler analiz ediliyor...</div>';
        
        const propsGrid = document.getElementById('player-props-grid');
        if (propsGrid) propsGrid.innerHTML = '<div class="loading-spinner"><i class="fa-solid fa-circle-notch fa-spin"></i> Patlama radarı güncelleniyor...</div>';
        
        fetchMatches(true);
        fetchPlayerProps();
    });

    document.querySelectorAll('.nav-item').forEach(item => {
        item.addEventListener('click', (e) => {
            e.preventDefault();
            document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
            item.classList.add('active');
            const targetId = item.getAttribute('data-page');
            document.querySelectorAll('.page-view').forEach(page => page.style.display = 'none');
            const pageEl = document.getElementById(targetId);
            if(pageEl) pageEl.style.display = 'block';

            if (targetId === 'page-live') {
                fetchMatches(true);
                fetchPlayerProps();
                fetchCoupons();
            } else if (targetId === 'page-upcoming') {
                fetchMatches(false);
            }
        });
    });

    // Baron Raporu Toggle Listener
    document.addEventListener('click', (e) => {
        if (e.target.closest('.toggle-analysis')) {
            const btn = e.target.closest('.toggle-analysis');
            const card = btn.closest('.match-card');
            const content = card.querySelector('.baron-report-content');
            
            if (content.style.maxHeight) {
                content.style.maxHeight = null;
                btn.innerHTML = '<i class="fa-solid fa-chevron-down"></i> Analizi Gör';
            } else {
                content.style.maxHeight = content.scrollHeight + "px";
                btn.innerHTML = '<i class="fa-solid fa-chevron-up"></i> Analizi Gizle';
            }
        }
    });

    // Search Input Listener
    const searchInput = document.getElementById('matchSearch');
    if (searchInput) {
        searchInput.addEventListener('input', (e) => {
            handleSearch(e.target.value);
        });
    }
});

function handleSearch(query) {
    if (!query || query.trim() === '') {
        // If search is empty, show default recommended matches in live feed or all matches in upcoming
        const isLivePage = document.querySelector('.nav-item.active').getAttribute('data-page') === 'page-live';
        if (isLivePage) {
            const recommended = window.allMatches.filter(m => m.ai_analysis && m.ai_analysis.is_recommended);
            renderMatches(recommended, true, 'matches-grid');
        } else {
            renderMatches(window.allMatches, false, 'upcoming-grid');
        }
        return;
    }

    const q = query.toLowerCase().trim();
    const filtered = window.allMatches.filter(m => 
        m.home_team.toLowerCase().includes(q) || 
        m.away_team.toLowerCase().includes(q) || 
        (m.sport_title && m.sport_title.toLowerCase().includes(q)) ||
        (m.sport_key && m.sport_key.toLowerCase().includes(q))
    );

    // Render search results to whichever grid is currently relevant or primarily to the live grid
    const activePage = document.querySelector('.nav-item.active').getAttribute('data-page');
    const targetGrid = activePage === 'page-upcoming' ? 'upcoming-grid' : 'matches-grid';
    
    renderMatches(filtered, false, targetGrid);
}

async function fetchMatches(recommendedOnly = true) {
    const gridId = recommendedOnly ? 'matches-grid' : 'upcoming-grid';
    const grid = document.getElementById(gridId);
    if (!grid) return;
    
    grid.innerHTML = '<div class="loading-spinner"><i class="fa-solid fa-circle-notch fa-spin"></i> Canlı ve gelecek veriler analiz ediliyor...</div>';

    try {
        const url = recommendedOnly ? `${API_BASE_URL}/api/odds/upcoming?recommended=true&t=${Date.now()}` : `${API_BASE_URL}/api/odds/upcoming?t=${Date.now()}`;
        const response = await fetch(url);
        if (!response.ok) throw new Error('API fetching failed');
        const data = await response.json();
        
        let matches = Array.isArray(data) ? data : (data.matches || []);
        
        // Update global cache for search
        window.allMatches = matches;
        
        if (recommendedOnly) {
            matches = matches.filter(m => m.ai_analysis && m.ai_analysis.is_recommended);
        }
        
        if (matches.length === 0) {
            grid.innerHTML = '<div class="glass-panel" style="padding: 20px; color: var(--text-secondary);">Şu an için uygun maç bulunamadı. Lütfen "Upcoming" sekmesini kontrol edin veya sayfayı yenileyin.</div>';
            return;
        }

        matches.sort((a, b) => {
            if (a.ai_analysis && b.ai_analysis) {
                if (a.ai_analysis.is_recommended && !b.ai_analysis.is_recommended) return -1;
                if (!a.ai_analysis.is_recommended && b.ai_analysis.is_recommended) return 1;
                return (a.ai_analysis.risk_score || 0) - (b.ai_analysis.risk_score || 0);
            }
            return 0;
        });

        renderMatches(matches, recommendedOnly, gridId);
    } catch (error) {
        console.error('Error fetching matches:', error);
        grid.innerHTML = `<div class="glass-panel" style="padding: 20px; color: #ef4444; border-left: 4px solid #ef4444;"><i class="fa-solid fa-triangle-exclamation"></i> Error connecting to AI core.<br><small style="color:var(--text-secondary)">Make sure the backend is running.</small></div>`;
    }
}

function pickLabelTr(target, home, away) {
    if (!target || target === 'N/A') return 'Değer yok — sadece analiz';
    const map = {HOME_WIN: '1 · ' + home, DRAW: 'X · Beraberlik', AWAY_WIN: '2 · ' + away};
    if (map[target]) return map[target];
    const parts = String(target).split(' ');
    if (parts.length === 2) return parts[1] + ' ' + (parts[0].toUpperCase() === 'OVER' ? 'Üst' : 'Alt');
    return target;
}

function renderMatches(matches, recommendedOnly, targetId) {
    const grid = document.getElementById(targetId);
    if (!grid) return;

    if (recommendedOnly) {
        const liveCountEl = document.getElementById('live-count');
        if (liveCountEl) liveCountEl.innerText = matches.length;
    }

    if (matches.length === 0) {
        grid.innerHTML = `<div class="glass-panel" style="padding: 20px; grid-column: 1 / -1; text-align: center; color: var(--text-secondary);"><i class="fa-solid fa-scale-balanced"></i> Önümüzdeki 24 saatte Pinnacle'a göre değerli seçim yok. Değer yoksa bahis alınmaz; maç analizleri "Upcoming" sayfasında.</div>`;
        return;
    }

    grid.innerHTML = '';
    matches.forEach(match => {
        const ai = match.ai_analysis || {};
        const isValueMethod = ai.method === 'value_vs_pinnacle';
        const isAnalyzed = isValueMethod || ai.risk_score > 0;
        const recommended = isValueMethod && ai.is_recommended;

        const dateStr = match.commence_time ? new Date(match.commence_time).toLocaleString('tr-TR', {
            hour: '2-digit', minute: '2-digit', day: '2-digit', month: 'short'
        }) : 'Canlı/Yakında';

        const color = recommended ? '#10b981' : '#94a3b8';
        let badgeHtml;
        if (recommended) {
            badgeHtml = `<div class="match-badge recommended"><i class="fa-solid fa-gem"></i> Değerli seçim${ai.bet_placed ? ' · bahis alındı' : ''}</div>`;
        } else if (isAnalyzed) {
            badgeHtml = `<div class="match-badge analyzing"><i class="fa-solid fa-chart-simple"></i> Analiz</div>`;
        } else {
            badgeHtml = `<div class="match-badge" style="cursor:pointer;" onclick="analyzeMatch('${match.id}', '${match.home_team.replace(/'/g, "\\'")}', '${match.away_team.replace(/'/g, "\\'")}')"><i class="fa-solid fa-magnifying-glass"></i> Analiz Et</div>`;
        }

        const card = document.createElement('div');
        card.className = 'match-card';
        if (!isAnalyzed) card.style.opacity = '0.7';

        let analysisHtml = '';
        if (isAnalyzed) {
            const pFair = Number(ai.p_fair != null ? ai.p_fair * 100 : ai.win_probability || 0);
            const ev = ai.ev != null ? (ai.ev * 100) : null;
            const pickText = isValueMethod ? pickLabelTr(ai.bet_target, match.home_team, match.away_team) : (ai.bet_target || 'N/A');
            const priceText = recommended ? ` @ ${(ai.odds_value || 0).toFixed(2)}` : '';
            const bookText = recommended && ai.bookmaker ? `<span style="font-size:11px; color:var(--text-secondary); font-weight:500;"> · ${escapeHtmlText(ai.bookmaker)}</span>` : '';
            const stakeText = ai.bet_placed && ai.bet_amount ? `<div style="font-size:11px; color:var(--text-secondary); margin-top:4px;">Sanal bahis: ${Number(ai.bet_amount).toFixed(0)} BB</div>` : '';
            const metrics = isValueMethod ? `
                <div style="display:flex; justify-content:space-between; font-size:11px; color:var(--text-secondary); margin-bottom:6px;">
                    <span>Pinnacle adil olasılık <b style="color:var(--text-primary);">%${pFair.toFixed(1)}</b></span>
                    ${ev != null ? `<span>Beklenen değer <b style="color:${ev >= 0 ? '#10b981' : '#ef4444'};">${ev >= 0 ? '+' : ''}${ev.toFixed(1)}%</b></span>` : ''}
                </div>
                <div class="progress-bar"><div class="progress-fill" style="width:${Math.min(100, pFair)}%; background:${color};"></div></div>` : `
                <div style="font-size:11px; color:var(--text-secondary);">Eski analiz (LLM)</div>`;
            analysisHtml = `
            <div class="ai-analysis">
                <div style="font-size: 11px; color: var(--text-secondary); margin-bottom: 6px;">${recommended ? 'Seçim' : 'Karar'}</div>
                <div class="target" style="color:${color}; font-weight:800; font-size:15px;">${escapeHtmlText(pickText)}${priceText}${bookText}</div>
                ${stakeText}
                <div style="margin-top: 12px; background: rgba(0,0,0,0.2); padding: 10px; border-radius: 8px;">${metrics}</div>
                <div class="baron-report-container" style="margin-top: 12px;">
                    <button class="toggle-analysis" style="width: 100%; background: rgba(255,255,255,0.05); border: 1px solid var(--panel-border); color: var(--text-secondary); padding: 8px; border-radius: 6px; cursor: pointer; font-size: 12px; font-weight: 600; display: flex; align-items: center; justify-content: center; gap: 8px;">
                        <i class="fa-solid fa-chevron-down"></i> Analizi Gör
                    </button>
                    <div class="baron-report-content" style="max-height: 0; overflow: hidden; transition: max-height 0.3s ease-out; background: rgba(0,0,0,0.2); border-radius: 0 0 8px 8px; font-size: 12px; line-height: 1.6; color: #cbd5e1;">
                        <div style="padding: 15px; border-left: 3px solid ${color}; white-space: pre-line;">${escapeHtmlText(ai.analysis || 'Analiz detayı bulunamadı.')}</div>
                    </div>
                </div>
            </div>`;
        }

        card.innerHTML = `
            ${badgeHtml}
            <div class="match-league">${escapeHtmlText(match.sport_title || match.sport_key)}</div>
            <div class="match-time"><i class="fa-regular fa-clock"></i> ${dateStr}</div>
            <div class="match-teams">
                <div class="team">${escapeHtmlText(match.home_team)}</div>
                <div style="font-size: 11px; color: var(--text-secondary); margin: 4px 0;">vs</div>
                <div class="team">${escapeHtmlText(match.away_team)}</div>
            </div>
            ${analysisHtml}
        `;
        grid.appendChild(card);
    });
}

// Modal Functions
function openModal(matchId, explanation) {
    const modal = document.getElementById('analysis-modal');
    const content = document.getElementById('modal-analysis-content');
    content.innerHTML = explanation ? `<div style="line-height:1.6; color:#e2e8f0; font-size:14px;">${explanation.replace(/\n/g, '<br>')}</div>` : 'Analiz detayı bulunamadı.';
    modal.style.display = 'block';
}

function closeModal() {
    document.getElementById('analysis-modal').style.display = 'none';
}

window.onclick = function(event) {
    const modal = document.getElementById('analysis-modal');
    if (event.target == modal) {
        modal.style.display = 'none';
    }
}

async function analyzeMatch(eventId, homeTeam, awayTeam) {
    const btn = event.currentTarget;
    btn.innerHTML = '<i class="fa-solid fa-circle-notch fa-spin"></i> Analiz Ediliyor';
    btn.style.pointerEvents = 'none';
    
    try {
        const res = await fetch(`${API_BASE_URL}/api/analyze/${eventId}`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ home_team: homeTeam, away_team: awayTeam })
        });
        
        if(res.ok) {
            fetchMatches(false); // Refresh upcoming page
        } else {
            btn.innerHTML = 'Hata Oluştu';
            setTimeout(() => { btn.innerHTML = '<i class="fa-solid fa-magnifying-glass"></i> Analiz Et'; btn.style.pointerEvents = 'auto'; }, 2000);
        }
    } catch(e) {
        btn.innerHTML = 'Bağlantı Hatası';
        setTimeout(() => { btn.innerHTML = '<i class="fa-solid fa-magnifying-glass"></i> Analiz Et'; btn.style.pointerEvents = 'auto'; }, 2000);
    }
}

async function fetchLogs() {
    try {
        const res = await fetch(`${API_BASE_URL}/api/logs`);
        if (res.ok) {
            const data = await res.json();
            const logsContainer = document.getElementById('log-body');
            if(logsContainer) {
                logsContainer.innerHTML = data.logs;
                logsContainer.scrollTop = logsContainer.scrollHeight;
            }
        }
    } catch(e) {}
}

let balancePoller = null;
async function fetchBetHistory() {
    try {
        const res = await fetch(`${API_BASE_URL}/api/bets/history?t=${Date.now()}`);
        if(res.ok) {
            const data = await res.json();
            updateBalance(data.current_balance, data.profit, data.bets);
        }
    } catch(e) {
        console.error("Balance fetch error:", e);
    }
}

function startBalancePoller() {
    if(!balancePoller) {
        balancePoller = setInterval(() => {
            fetchBetHistory();
            fetchMatches(true);
            fetchPlayerProps();
        }, 15000); // Poll every 15 seconds
    }
}

document.addEventListener('DOMContentLoaded', () => {
    startBalancePoller();
});

function updateBalance(balance, profit, bets) {
    const balEl = document.getElementById('baron-balance');
    const profEl = document.getElementById('baron-profit');
    if(balEl) {
        const formatBal = parseFloat(balance).toFixed(2);
        balEl.innerText = `${formatBal} BB`;
        
        if (profit > 0) {
            profEl.innerHTML = `<span style="color: #10b981;"><i class="fa-solid fa-arrow-trend-up"></i> +${profit.toFixed(2)} BB</span>`;
        } else if (profit < 0) {
            profEl.innerHTML = `<span style="color: #ef4444;"><i class="fa-solid fa-arrow-trend-down"></i> ${profit.toFixed(2)} BB</span>`;
        } else {
            profEl.innerHTML = `<span style="color: #94a3b8;"><i class="fa-solid fa-minus"></i> 0.00 BB</span>`;
        }
    }
    
    // YZ Başarı Oranı Dinamik Hesaplama
    const accuracyEl = document.getElementById('ai-accuracy');
    if (accuracyEl) {
        const resolvedBets = (bets || []).filter(b => b.status === 'WON' || b.status === 'LOST');
        const wonBets = resolvedBets.filter(b => b.status === 'WON');
        if (resolvedBets.length > 0) {
            const acc = (wonBets.length / resolvedBets.length) * 100;
            accuracyEl.innerText = `${acc.toFixed(1)}%`;
        } else {
            accuracyEl.innerText = `0.0%`;
        }
    }

    renderBalanceHistoryChart(bets);
    renderPendingBets(bets);
    renderHistoryList(bets);
}

let roiChartInstance = null;

function renderBalanceHistoryChart(bets) {
    const ctx = document.getElementById('roiChart');
    if (!ctx) return;

    const historyLines = [...(bets || [])]
        .filter(b => b.status === 'WON' || b.status === 'LOST')
        .sort((a,b) => new Date(a.created_at.replace(" ", "T")) - new Date(b.created_at.replace(" ", "T")));

    let currentBal = 10000;
    const labels = ['Başlangıç'];
    const dataPoints = [currentBal];

    historyLines.forEach((b, i) => {
        labels.push(`Bahis #${i+1}`);
        if(b.status === 'WON') {
            currentBal += b.profit;
        } else if (b.status === 'LOST') {
            currentBal -= b.bet_amount;
        }
        dataPoints.push(currentBal);
    });

    if (roiChartInstance) {
        roiChartInstance.destroy();
    }

    roiChartInstance = new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [{
                label: 'Baron Kasa (BB)',
                data: dataPoints,
                borderColor: '#3b82f6',
                backgroundColor: 'rgba(59, 130, 246, 0.1)',
                borderWidth: 2,
                tension: 0.3,
                fill: true,
                pointRadius: 3,
                pointBackgroundColor: '#fff'
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { labels: { color: '#94a3b8' } }
            },
            scales: {
                x: { ticks: { color: '#94a3b8' }, grid: { color: 'rgba(255,255,255,0.05)' } },
                y: { ticks: { color: '#94a3b8' }, grid: { color: 'rgba(255,255,255,0.05)' } }
            }
        }
    });
}

function renderPendingBets(bets) {
    const list = document.getElementById('bets-list');
    if (!list) return;

    const pending = (bets || []).filter(b => b.status === 'PENDING')
        .sort((a,b) => new Date(b.created_at.replace(" ", "T")) - new Date(a.created_at.replace(" ", "T")));
    list.innerHTML = '';
    
    if (pending.length === 0) {
        list.innerHTML = '<div style="color:var(--text-secondary); font-size:12px; padding:10px;">Bekleyen bahis yok.</div>';
        return;
    }

    pending.forEach(b => {
        const item = document.createElement('div');
        item.style.padding = '10px';
        item.style.background = 'rgba(255,255,255,0.02)';
        item.style.borderRadius = '8px';
        item.style.marginBottom = '8px';
        item.innerHTML = `
            <div style="display:flex; justify-content:space-between; margin-bottom:4px;">
                <span style="font-size:11px; color:var(--text-secondary);">${b.sport_key || b.sport_title || ''}</span>
                <span style="font-size:11px; color:#f59e0b;"><i class="fa-solid fa-clock"></i> PENDING</span>
            </div>
            <div style="font-size:13px; font-weight:600; margin-bottom:4px;">${b.home_team} vs ${b.away_team}</div>
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <span style="color:#3b82f6; font-size:12px;">Hedef: ${b.bet_target || 'N/A'} @ ${(b.odds_value||0).toFixed(2)}</span>
                <span style="color:var(--text-secondary); font-size:11px;">Miktar: ${b.bet_amount} BB</span>
            </div>
        `;
        list.appendChild(item);
    });
}

function renderHistoryList(bets) {
    const list = document.getElementById('history-list');
    if (!list) return;

    // Use replace(" ", "T") to ensure cross-browser parsing for SQLite datetime
    const finished = (bets || []).filter(b => ['WON', 'LOST', 'VOID'].includes(b.status))
        .sort((a,b) => new Date(b.created_at.replace(" ", "T")) - new Date(a.created_at.replace(" ", "T")));
    list.innerHTML = '';
    
    if (finished.length === 0) {
        list.innerHTML = '<div style="color:var(--text-secondary); font-size:12px; padding:10px;">Henüz sonuçlanan bahis yok.</div>';
        return;
    }

    // VOID = oyuncu oynamadı, bahis iade (gri)
    const statusColor = s => s === 'WON' ? '#10b981' : (s === 'VOID' ? '#9ca3af' : '#ef4444');

    finished.forEach(b => {
        const item = document.createElement('div');
        item.style.padding = '12px';
        item.style.background = 'rgba(255,255,255,0.02)';
        item.style.borderRadius = '8px';
        item.style.marginBottom = '8px';
        item.style.borderLeft = `4px solid ${statusColor(b.status)}`;
        
        item.innerHTML = `
            <div style="display:flex; justify-content:space-between; margin-bottom:4px;">
                <span style="font-size:11px; color:var(--text-secondary);">${b.sport_key || ''}</span>
                <span style="font-size:11px; font-weight:bold; color:${statusColor(b.status)};">${b.status}</span>
            </div>
            <div style="font-size:13px; font-weight:600; margin-bottom:4px;">${b.home_team} vs ${b.away_team}</div>
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <span style="color:var(--text-secondary); font-size:12px;">Hedef: ${b.bet_target || 'N/A'} @ ${(b.odds_value||0).toFixed(2)}</span>
                <span style="font-size:12px; font-weight:bold; color:${statusColor(b.status)};">${b.status==='WON'?'+':''}${b.profit ? b.profit.toFixed(2) : ''} BB</span>
            </div>
        `;
        list.appendChild(item);
    });
}

async function fetchPlayerProps() {
    const grid = document.getElementById("player-props-grid");
    if (!grid) return;
    try {
        const res = await fetch(API_BASE_URL + "/api/nba/player-props?t=" + Date.now());
        if (!res.ok) throw new Error("HTTP " + res.status);
        const props = await res.json();
        
        if (!props || props.length === 0) {
            grid.innerHTML = "<div class='glass-panel' style='padding:20px; color:var(--text-secondary); grid-column:1/-1; text-align:center;'><i class='fa-solid fa-magnifying-glass'></i> Henüz edge'li prop bulunamadı (model piyasadan anlamlı farklı görmüyorsa öneri çıkmaz).<br><small>Arka plandaki analiz döngüsü yaklaşık 3-5 dakika sürebilir. Yeni verileri görmek için sayfayı yenile butonunu kullanın.</small></div>";
            return;
        }
        const statLabels = { player_points: "Puan", player_rebounds: "Ribaund", player_assists: "Asist" };
        grid.innerHTML = props.map(function(p) {
            const conf = p.confidence || 0;
            const col = conf >= 80 ? "#10b981" : conf >= 70 ? "#f59e0b" : "#94a3b8";
            const statLabel = statLabels[p.market] || p.stat;
            return "<div class='glass-panel' style='padding:18px; border-radius:16px; border-left:4px solid " + col + "; position:relative;'>" +
                "<div style='position:absolute; top:10px; right:12px; background:rgba(16,185,129,0.15); color:#10b981; padding:2px 8px; border-radius:12px; font-size:10px; font-weight:700;'>MODEL</div>" +
                "<div style='font-size:11px; color:var(--text-secondary); margin-bottom:4px;'>" + p.home_team + " vs " + p.away_team + "</div>" +
                "<div style='font-size:18px; font-weight:700; margin-bottom:4px;'>" + p.player + "</div>" +
                "<div style='font-size:14px; color:" + col + "; font-weight:600; margin-bottom:12px;'>" + (p.side === 'UNDER' ? 'ALT ' : 'ÜST ') + p.line + " " + statLabel + " @ " + (p.odds || p.over_odds || 0).toFixed(2) + "</div>" +
                "<div style='background:rgba(255,255,255,0.04); border-radius:8px; padding:10px; margin-bottom:10px; font-size:12px;'>" +
                "<div style='display:flex; justify-content:space-between; margin-bottom:5px;'><span style='color:var(--text-secondary);'>Projeksiyon</span><span style='font-weight:600;'>" + (p.mu != null ? p.mu.toFixed(1) : "N/A") + " " + statLabel + " (" + (p.min_proj != null ? p.min_proj.toFixed(0) : "?") + " dk)</span></div>" +
                "<div style='display:flex; justify-content:space-between; margin-bottom:5px;'><span style='color:var(--text-secondary);'>Model / Piyasa</span><span>%" + Math.round((p.p_model||0)*100) + " / %" + Math.round((p.p_fair||0)*100) + "</span></div>" +
                "<div style='display:flex; justify-content:space-between;'><span style='color:var(--text-secondary);'>Edge</span><span style='color:#10b981; font-weight:700;'>+" + ((p.edge||0)*100).toFixed(1) + " puan</span></div>" +
                "<div style='border-top:1px solid rgba(255,255,255,0.05); margin-top:8px; padding-top:8px; display:flex; flex-direction:column; gap:4px;'>" +
                "<div style='display:flex; justify-content:space-between; font-size:10px; color:var(--text-secondary); opacity:0.7; padding:0 4px;'>" +
                "<span>Maç / Tarih</span>" +
                "<span style='display:flex; gap:12px; width:80px; justify-content:center;'><span>PTS</span><span>REB</span><span>AST</span></span></div>" +
                (p.last_games_detail || []).map(function(g) {
                    var isOver = g.value > p.line;
                    var valColor = isOver ? '#22c55e' : '#ef4444';
                    var pColor = p.stat === 'PTS' ? valColor : 'var(--text-primary)';
                    var rColor = p.stat === 'REB' ? valColor : 'var(--text-primary)';
                    var aColor = p.stat === 'AST' ? valColor : 'var(--text-primary)';
                    return "<div style='display:flex; justify-content:space-between; align-items:center; font-size:11px; padding:4px; background:rgba(255,255,255,0.02); border-radius:4px; color:var(--text-secondary);'>" +
                        "<span>🗓️ " + g.date.substring(0,6) + " (" + g.matchup + ")</span>" +
                        "<span style='display:flex; gap:12px; width:80px; justify-content:center; font-weight:600; font-family:monospace;'>" + 
                        "<span style='color:" + pColor + "; width:20px; text-align:center;'>" + (g.pts!==undefined?g.pts:'-') + "</span>" +
                        "<span style='color:" + rColor + "; width:20px; text-align:center;'>" + (g.reb!==undefined?g.reb:'-') + "</span>" +
                        "<span style='color:" + aColor + "; width:20px; text-align:center;'>" + (g.ast!==undefined?g.ast:'-') + "</span>" +
                        "</span></div>";
                }).join("") +
                "</div></div>" +
                "<div style='font-size:11px; color:var(--text-secondary); margin-bottom:10px; line-height:1.5;'>" + p.reason + "</div>" +
                "<div style='display:flex; justify-content:space-between; align-items:center;'>" +
                "<div><div style='font-size:10px; color:var(--text-secondary);'>Guven</div><div style='font-weight:700; font-size:16px; color:" + col + ";'>" + conf + "%</div></div>" +
                "<div style='text-align:right;'><div style='font-size:10px; color:var(--text-secondary);'>Bahis</div><div style='font-weight:700; font-size:16px; color:var(--accent-primary);'>" + (p.bet_amount||75) + " BB</div></div></div></div>";
        }).join("");
    } catch(e) {
        if (grid) grid.innerHTML = "<div class='glass-panel' style='padding:20px; color:var(--text-secondary); grid-column:1/-1; text-align:center;'><i class='fa-solid fa-triangle-exclamation'></i> Oyuncu prop verisi yuklenemedi: " + e.message + "</div>";
    }
}


// ---------------------------------------------------------------- Günün kuponları (/api/coupons)
function escapeHtmlText(t) {
    return String(t == null ? '' : t).replace(/[&<>"']/g, function(c) {
        return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c];
    });
}

function couponLegLabel(l) {
    if (l.player) {
        const stat = {PTS: 'Sayı', REB: 'Ribaund', AST: 'Asist'}[l.stat] || l.stat;
        return l.player + ' — ' + stat + ' ' + l.line + ' ' + (l.side === 'OVER' ? 'Üst' : 'Alt');
    }
    const map = {HOME_WIN: '1 (Ev sahibi)', DRAW: 'X (Beraberlik)', AWAY_WIN: '2 (Deplasman)'};
    if (map[l.pick]) return map[l.pick];
    const parts = String(l.pick).split(' ');
    return parts[1] + ' ' + (parts[0] === 'OVER' ? 'Üst' : 'Alt');
}

async function fetchCoupons() {
    const grid = document.getElementById('coupons-grid');
    if (!grid) return;
    try {
        const res = await fetch(API_BASE_URL + '/api/coupons?t=' + Date.now());
        if (!res.ok) throw new Error('HTTP ' + res.status);
        const data = await res.json();
        const coupons = data.coupons || [];
        if (coupons.length === 0) {
            grid.innerHTML = "<div class='glass-panel' style='padding:20px; color:var(--text-secondary); grid-column:1/-1; text-align:center;'>Henüz kupon yok. Futbol kuponu her gün 12:00'de, NBA kuponu 00:40'ta (TR) hazırlanır; değer yoksa kupon çıkmaz.</div>";
            return;
        }
        const statusColor = {WON: '#10b981', LOST: '#ef4444', VOID: '#94a3b8', PENDING: '#f59e0b'};
        const legMark = {WON: '✅', LOST: '❌', VOID: '⚪'};
        grid.innerHTML = coupons.map(function(c) {
            const col = statusColor[c.status] || '#94a3b8';
            const title = c.kind === 'football' ? '⚽ Futbol kuponu' : '🏀 NBA kuponu (3 oyuncu)';
            const legs = (c.legs || []).map(function(l) {
                const t = new Date(l.commence_time).toLocaleString('tr-TR', {day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'});
                return "<div style='padding:6px 0; border-bottom:1px solid rgba(255,255,255,0.05); font-size:12px;'>" +
                    "<div style='color:var(--text-secondary);'>" + (legMark[l.result] || '⏳') + ' ' + t + ' · ' + escapeHtmlText(l.home_team) + ' - ' + escapeHtmlText(l.away_team) + "</div>" +
                    "<div style='display:flex; justify-content:space-between;'><span style='font-weight:600;'>" + escapeHtmlText(couponLegLabel(l)) + "</span><span>@ " + Number(l.odds).toFixed(2) + "</span></div></div>";
            }).join('');
            return "<div class='glass-panel' style='padding:16px; border-radius:16px; border-left:4px solid " + col + ";'>" +
                "<div style='display:flex; justify-content:space-between; margin-bottom:6px;'><span style='font-weight:700;'>" + title + "</span><span style='color:" + col + "; font-weight:700; font-size:12px;'>" + c.status + "</span></div>" +
                "<div style='font-size:11px; color:var(--text-secondary); margin-bottom:8px;'>" + c.coupon_date + ' · ' + escapeHtmlText(c.bookmaker) + "</div>" + legs +
                "<div style='display:flex; justify-content:space-between; margin-top:10px; font-size:13px;'>" +
                "<span>Toplam oran <b>" + Number(c.total_odds).toFixed(2) + "</b></span>" +
                "<span>Tutma olasılığı <b>%" + (Number(c.p_fair || 0) * 100).toFixed(1) + "</b></span>" +
                "<span>EV <b>" + (Number(c.ev || 0) * 100).toFixed(1) + "%</b></span></div>" +
                (c.status !== 'PENDING' ? "<div style='margin-top:6px; font-size:12px; color:" + col + ";'>Kâr/Zarar: " + Number(c.profit).toFixed(2) + " BB</div>" : '') +
                "</div>";
        }).join('');
        const s = data.summary || {};
        if (s.resolved) {
            grid.insertAdjacentHTML('afterbegin', "<div class='glass-panel' style='padding:12px 16px; grid-column:1/-1; font-size:12px; color:var(--text-secondary);'>Sonuçlanan kupon: " + s.resolved + ' · tutan: ' + s.won + ' · toplam kâr/zarar: ' + Number(s.profit).toFixed(2) + ' BB</div>');
        }
    } catch (e) {
        grid.innerHTML = "<div class='glass-panel' style='padding:20px; color:var(--text-secondary); grid-column:1/-1;'>Kuponlar yüklenemedi: " + escapeHtmlText(e.message) + "</div>";
    }
}
