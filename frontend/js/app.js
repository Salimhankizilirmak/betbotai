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

function renderMatches(matches, recommendedOnly, targetId) {
    const grid = document.getElementById(targetId);
    if (!grid) return;

    // Tracked Matches Dinamik Guncellemesi
    if (recommendedOnly) {
        const liveCountEl = document.getElementById('live-count');
        if (liveCountEl) {
            liveCountEl.innerText = matches.length;
        }
    }

    if (matches.length === 0) {
        grid.innerHTML = `<div class="glass-panel" style="padding: 20px; grid-column: 1 / -1; text-align: center; color: var(--text-secondary);"><i class="fa-solid fa-brain"></i> Baron şu an bülteni tarıyor olabilir, veya yüksek güvenli maç bulunamadı.</div>`;
        return;
    }

    grid.innerHTML = '';
    matches.forEach(match => {
        const ai = match.ai_analysis || {};
        const isAnalyzed = ai.risk_score > 0;
        
        const betAmount = ai.bet_amount || 100;
        
        let riskClass = 'risk-medium';
        if (ai.risk_score < 30) riskClass = 'risk-low';
        else if (ai.risk_score > 60) riskClass = 'risk-high';

        let targetColor = '#3b82f6';
        if (ai.bet_target && (ai.bet_target.includes('OVER') || ai.bet_target === 'HOME_WIN')) targetColor = '#10b981';
        if (ai.bet_target && (ai.bet_target.includes('UNDER') || ai.bet_target === 'AWAY_WIN')) targetColor = '#f59e0b';

        let badgeHtml = '';
        if (isAnalyzed) {
            badgeHtml = `
            <div class="match-badge ${ai.is_recommended ? 'recommended' : 'analyzing'}">
                <i class="fa-solid ${ai.is_recommended ? 'fa-fire' : 'fa-spinner fa-spin'}"></i>
                ${ai.is_recommended ? 'Baron Onaylı' : 'Analiz Ediliyor'}
            </div>`;
        } else {
            badgeHtml = `
            <div class="match-badge" style="cursor:pointer;" onclick="analyzeMatch('${match.id}', '${match.home_team.replace(/'/g, "\\'")}', '${match.away_team.replace(/'/g, "\\'")}')">
                <i class="fa-solid fa-magnifying-glass"></i> Analiz Et
            </div>`;
        }

        const dateStr = match.commence_time ? new Date(match.commence_time).toLocaleString('tr-TR', {
            hour: '2-digit', minute: '2-digit', day: '2-digit', month: 'short'
        }) : 'Canlı/Yakında';

        const card = document.createElement('div');
        card.className = 'match-card';
        if (!isAnalyzed) {
            card.style.opacity = '0.7';
        }

        let analysisHtml = '';
        if (isAnalyzed) {
            analysisHtml = `
            <div class="ai-analysis">
                <div style="font-size: 11px; color: var(--text-secondary); margin-bottom: 8px;">Baron'un Kesin Kararı (${betAmount} BB)</div>
                <div class="target" style="color: ${targetColor}; font-weight: 800; font-size: 16px;">
                    ${ai.bet_target || 'N/A'} @ ${(ai.odds_value || 0).toFixed(2)}
                </div>
                
                <div style="margin-top: 15px; background: rgba(0,0,0,0.2); padding: 10px; border-radius: 8px;">
                    <div style="font-size: 11px; color: var(--text-secondary); margin-bottom: 6px; display: flex; justify-content: space-between;">
                        <span>Güven Skoru</span>
                        <span style="color: var(--text-primary); font-weight: 600;">%${(ai.win_probability || 0).toFixed(0)}</span>
                    </div>
                    <div class="progress-bar">
                        <div class="progress-fill" style="width: ${Math.min(100, ai.win_probability || 0)}%; background: ${targetColor};"></div>
                    </div>
                </div>
                
                <div class="baron-report-container" style="margin-top: 15px;">
                    <button class="toggle-analysis" style="width: 100%; background: rgba(255,255,255,0.05); border: 1px solid var(--panel-border); color: var(--text-secondary); padding: 8px; border-radius: 6px; cursor: pointer; font-size: 12px; font-weight: 600; transition: all 0.3s; display: flex; align-items: center; justify-content: center; gap: 8px;">
                        <i class="fa-solid fa-chevron-down"></i> Analizi Gör
                    </button>
                    <div class="baron-report-content" style="max-height: 0; overflow: hidden; transition: max-height 0.3s ease-out; background: rgba(0,0,0,0.2); border-radius: 0 0 8px 8px; font-size: 12px; line-height: 1.6; color: #cbd5e1;">
                        <div style="padding: 15px; border-left: 3px solid ${targetColor};">
                            <div style="font-weight: bold; margin-bottom: 8px; color: #fff; display: flex; align-items: center; gap: 6px;">
                                <i class="fa-solid fa-brain"></i> Baron Raporu
                            </div>
                            ${ai.analysis ? ai.analysis.replace(/\n/g, '<br>') : 'Analiz detayı bulunamadı.'}
                        </div>
                    </div>
                </div>
            </div>`;
        }

        card.innerHTML = `
            ${badgeHtml}
            <div class="match-league">${match.sport_title || match.sport_key}</div>
            <div class="match-time"><i class="fa-regular fa-clock"></i> ${dateStr}</div>
            <div class="match-teams">
                <div class="team">${match.home_team}</div>
                <div style="font-size: 11px; color: var(--text-secondary); margin: 4px 0;">vs</div>
                <div class="team">${match.away_team}</div>
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
    const finished = (bets || []).filter(b => b.status === 'WON' || b.status === 'LOST')
        .sort((a,b) => new Date(b.created_at.replace(" ", "T")) - new Date(a.created_at.replace(" ", "T")));
    list.innerHTML = '';
    
    if (finished.length === 0) {
        list.innerHTML = '<div style="color:var(--text-secondary); font-size:12px; padding:10px;">Henüz sonuçlanan bahis yok.</div>';
        return;
    }

    finished.forEach(b => {
        const item = document.createElement('div');
        item.style.padding = '12px';
        item.style.background = 'rgba(255,255,255,0.02)';
        item.style.borderRadius = '8px';
        item.style.marginBottom = '8px';
        item.style.borderLeft = b.status === 'WON' ? '4px solid #10b981' : '4px solid #ef4444';
        
        item.innerHTML = `
            <div style="display:flex; justify-content:space-between; margin-bottom:4px;">
                <span style="font-size:11px; color:var(--text-secondary);">${b.sport_key || ''}</span>
                <span style="font-size:11px; font-weight:bold; color:${b.status==='WON'?'#10b981':'#ef4444'};">${b.status}</span>
            </div>
            <div style="font-size:13px; font-weight:600; margin-bottom:4px;">${b.home_team} vs ${b.away_team}</div>
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <span style="color:var(--text-secondary); font-size:12px;">Hedef: ${b.bet_target || 'N/A'} @ ${(b.odds_value||0).toFixed(2)}</span>
                <span style="font-size:12px; font-weight:bold; color:${b.status==='WON'?'#10b981':'#ef4444'};">${b.status==='WON'?'+':''}${b.profit ? b.profit.toFixed(2) : ''} BB</span>
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
            grid.innerHTML = "<div class='glass-panel' style='padding:20px; color:var(--text-secondary); grid-column:1/-1; text-align:center;'><i class='fa-solid fa-magnifying-glass'></i> Henüz patlama sinyali taranmadı veya bulunamadı.<br><small>Arka plandaki analiz döngüsü yaklaşık 3-5 dakika sürebilir. Yeni verileri görmek için sayfayı yenile butonunu kullanın.</small></div>";
            return;
        }
        const statLabels = { player_points: "Puan", player_rebounds: "Ribaund", player_assists: "Asist" };
        grid.innerHTML = props.map(function(p) {
            const conf = p.confidence || 0;
            const col = conf >= 80 ? "#10b981" : conf >= 70 ? "#f59e0b" : "#94a3b8";
            const statLabel = statLabels[p.market] || p.stat;
            return "<div class='glass-panel' style='padding:18px; border-radius:16px; border-left:4px solid " + col + "; position:relative;'>" +
                "<div style='position:absolute; top:10px; right:12px; background:rgba(245,158,11,0.15); color:#f59e0b; padding:2px 8px; border-radius:12px; font-size:10px; font-weight:700;'>PATLAMA</div>" +
                "<div style='font-size:11px; color:var(--text-secondary); margin-bottom:4px;'>" + p.home_team + " vs " + p.away_team + "</div>" +
                "<div style='font-size:18px; font-weight:700; margin-bottom:4px;'>" + p.player + "</div>" +
                "<div style='font-size:14px; color:" + col + "; font-weight:600; margin-bottom:12px;'>OVER " + p.line + " " + statLabel + " @ " + (p.over_odds||0).toFixed(2) + "</div>" +
                "<div style='background:rgba(255,255,255,0.04); border-radius:8px; padding:10px; margin-bottom:10px; font-size:12px;'>" +
                "<div style='display:flex; justify-content:space-between; margin-bottom:5px;'><span style='color:var(--text-secondary);'>Hat</span><span>" + p.line + " " + statLabel + "</span></div>" +
                "<div style='display:flex; justify-content:space-between; margin-bottom:5px;'><span style='color:var(--text-secondary);'>Son 3 ort.</span><span style='color:#ef4444; font-weight:600;'>" + (p.avg_last_n||"N/A") + " " + statLabel + "</span></div>" +
                "<div style='display:flex; justify-content:space-between;'><span style='color:var(--text-secondary);'>Eksik</span><span style='color:#f59e0b; font-weight:700;'>-" + (p.deficit||0) + " " + statLabel + "</span></div>" +
                "<div style='border-top:1px solid rgba(255,255,255,0.05); margin-top:8px; padding-top:8px; display:flex; flex-direction:column; gap:4px;'>" +
                "<div style='display:flex; justify-content:space-between; font-size:10px; color:var(--text-secondary); opacity:0.7; padding:0 4px;'>" +
                "<span>Maç / Tarih</span>" +
                "<span style='display:flex; gap:12px; width:80px; justify-content:center;'><span>PTS</span><span>REB</span><span>AST</span></span></div>" +
                (p.last_games_detail || []).map(function(g) {
                    var isUnder = g.value < p.line;
                    var valColor = isUnder ? '#22c55e' : '#ef4444';
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
