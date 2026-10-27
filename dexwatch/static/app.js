(() => {
  'use strict';

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
  const CHAIN_LABEL = { bsc: 'BSC', ethereum: 'ETH', base: 'BASE', arbitrum: 'ARB' };
  const CHAIN_NAME = {
    bsc: 'BNB Smart Chain', ethereum: 'Ethereum', base: 'Base', arbitrum: 'Arbitrum One',
  };
  const EXPLORER = {
    bsc: 'https://bscscan.com', ethereum: 'https://etherscan.io',
    base: 'https://basescan.org', arbitrum: 'https://arbiscan.io',
  };
  const DEXSCREENER = { bsc: 'bsc', ethereum: 'ethereum', base: 'base', arbitrum: 'arbitrum' };
  const SOCIAL_LABEL = { twitter: 'X', x: 'X', telegram: 'TG', discord: 'DC', website: 'WWW', github: 'GH' };

  const state = {
    chain: new URLSearchParams(location.search).get('chain') || 'bsc',
    chains: [],
    items: [],
    cards: new Map(),
    sort: localStorage.getItem('dw.sort') || 'new',
    q: '',
    minLiq: Number(localStorage.getItem('dw.minLiq') || 0),
    hideHp: localStorage.getItem('dw.hideHp') === '1',
    onlyVerified: localStorage.getItem('dw.verified') === '1',
    view: localStorage.getItem('dw.view') || 'grid',
    auto: localStorage.getItem('dw.auto') !== '0',
    loading: false,
    firstLoad: true,
    selected: null,
    chart: { tf: 'minute', agg: 1, candles: [], source: '', layout: null },
  };

  /* ------------------------------------------------------------- format */
  const nf = new Intl.NumberFormat('id-ID');
  const fmtUsd = (v, d = 2) => {
    v = Number(v);
    if (!isFinite(v) || v <= 0) return '–';
    if (v >= 1e9) return '$' + (v / 1e9).toFixed(d) + 'B';
    if (v >= 1e6) return '$' + (v / 1e6).toFixed(d) + 'M';
    if (v >= 1e3) return '$' + (v / 1e3).toFixed(d) + 'K';
    if (v >= 1) return '$' + v.toFixed(2);
    if (v === 0) return '$0';
    return '$' + v.toFixed(8).replace(/0+$/, '');
  };
  const fmtPrice = (v) => {
    v = Number(v);
    if (!isFinite(v) || v <= 0) return '–';
    if (v >= 1) return '$' + v.toFixed(v >= 100 ? 2 : 4);
    if (v >= 0.0001) return '$' + v.toFixed(6).replace(/0+$/, '');
    return '$' + v.toFixed(10).replace(/0+$/, '');
  };
  const fmtAge = (s) => {
    s = Math.max(0, Math.floor(Number(s) || 0));
    if (s < 60) return s + 's';
    if (s < 3600) return Math.floor(s / 60) + 'm';
    if (s < 86400) return Math.floor(s / 3600) + 'j';
    return Math.floor(s / 86400) + 'hr';
  };
  const ageText = (it) => (num(it.created_ts) > 0 ? fmtAge(it.age_seconds) : '–');
  const fmtNum = (v) => {
    v = Number(v);
    return isFinite(v) && v !== 0 ? nf.format(v) : '–';
  };
  const pct = (v, digits = 1) => {
    v = Number(v);
    if (!isFinite(v)) return '–';
    return (v > 0 ? '+' : '') + v.toFixed(digits) + '%';
  };
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
  const shortAddr = (a) => (a ? a.slice(0, 6) + '…' + a.slice(-4) : '–');
  const tokenLink = (it) => (EXPLORER[it.chain] || EXPLORER.bsc) + '/token/' + it.token;
  const poolLink = (it) => (EXPLORER[it.chain] || EXPLORER.bsc) + '/address/' + it.pool;
  const dsLink = (it) => 'https://dexscreener.com/' + (DEXSCREENER[it.chain] || it.chain) + '/' + it.pool;
  const hash = (s) => {
    let h = 2166136261;
    for (let i = 0; i < String(s).length; i++) { h ^= String(s).charCodeAt(i); h = Math.imul(h, 16777619); }
    return Math.abs(h);
  };

  function toast(text) {
    const el = $('#toast');
    el.textContent = text;
    el.classList.add('show');
    clearTimeout(toast._t);
    toast._t = setTimeout(() => el.classList.remove('show'), 1600);
  }

  function copyText(text) {
    const done = () => toast('Contract disalin');
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(done).catch(() => fallback());
    } else fallback();
    function fallback() {
      const ta = document.createElement('textarea');
      ta.value = text; document.body.appendChild(ta); ta.select();
      try { document.execCommand('copy'); done(); } catch (e) { toast('Gagal menyalin'); }
      ta.remove();
    }
  }

  /* --------------------------------------------------------------- state */
  function setLive(mode) {
    const dot = $('#live .dot');
    const text = $('#liveText');
    dot.className = 'dot' + (mode === 'sync' ? ' sync' : mode === 'off' ? ' off' : '');
    text.textContent = mode === 'sync' ? 'sync' : mode === 'off' ? 'offline' : 'live';
  }

  function visibleItems() {
    const q = state.q.toLowerCase().trim();
    const items = state.items.filter((it) => {
      if (state.minLiq && !(Number(it.liquidity_usd) >= state.minLiq)) return false;
      if (state.hideHp && it.honeypot) return false;
      if (state.onlyVerified && it.verified !== true) return false;
      if (q) {
        const hay = [it.symbol, it.name, it.token, it.pool, it.dex, it.quote_symbol]
          .join(' ').toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
    const key = state.sort;
    if (key === 'volume') items.sort((a, b) => num(b.volume?.h24) - num(a.volume?.h24));
    else if (key === 'liq') items.sort((a, b) => num(b.liquidity_usd) - num(a.liquidity_usd));
    else if (key === 'rise') items.sort((a, b) => num(b.change?.h24) - num(a.change?.h24));
    else if (key === 'fall') items.sort((a, b) => num(a.change?.h24) - num(b.change?.h24));
    else items.sort((a, b) => num(b.created_ts) - num(a.created_ts));
    return items;
  }
  const num = (v) => { const n = Number(v); return isFinite(n) ? n : 0; };

  /* ---------------------------------------------------------------- feed */
  async function loadFeed() {
    if (state.loading) return;
    state.loading = true;
    setLive('sync');
    try {
      const params = new URLSearchParams({ chain: state.chain, limit: '240', sort: 'new' });
      const res = await fetch('/api/feed?' + params, { cache: 'no-store' });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const data = await res.json();
      const prev = new Set(state.items.map((i) => i.pool));
      const incoming = data.pools || [];
      if (prev.size && incoming.length) {
        const fresh = incoming.filter((i) => !prev.has(i.pool));
        if (fresh.length) {
          toast(`Pool baru: ${fresh[0].symbol || '?'}` + (fresh.length > 1 ? ` +${fresh.length - 1}` : ''));
        }
      }
      state.items = incoming;
      state.chain = data.chain || state.chain;
      if (Array.isArray(data.chains) && data.chains.length) state.chains = data.chains;
      if (!state.chains.includes(state.chain)) state.chains.push(state.chain);
      renderChains();
      setLive('live');
    } catch (err) {
      setLive('off');
    } finally {
      state.loading = false;
      state.firstLoad = false;
    }
    render();
  }

  /* -------------------------------------------------------------- chains */
  function renderChains() {
    const box = $('#chains');
    const chains = state.chains.length ? state.chains : [state.chain];
    box.innerHTML = chains.map((c) => (
      `<button class="chain ${c === state.chain ? 'active' : ''}" data-chain="${esc(c)}">${esc(CHAIN_LABEL[c] || c)}</button>`
    )).join('');
    $$('.chain', box).forEach((btn) => {
      btn.onclick = () => {
        if (btn.dataset.chain === state.chain) return;
        state.chain = btn.dataset.chain;
        state.items = [];
        state.cards.clear();
        $('#grid').innerHTML = '';
        renderChains();
        loadFeed();
      };
    });
  }

  /* -------------------------------------------------------------- render */
  function render() {
    const items = visibleItems();
    $('#empty').classList.toggle('hidden', items.length > 0);
    renderStats(items);
    if (state.view === 'grid') {
      $('#grid').classList.remove('hidden');
      $('#list').classList.add('hidden');
      renderGrid(items);
    } else {
      $('#grid').classList.add('hidden');
      $('#list').classList.remove('hidden');
      renderList(items);
    }
  }

  function renderStats(items) {
    const total = items.length;
    const fresh = items.filter((i) => num(i.age_seconds) < 300).length;
    const liq = items.reduce((s, i) => s + num(i.liquidity_usd), 0);
    const age = total ? items.reduce((s, i) => s + num(i.age_seconds), 0) / total : 0;
    const changes = items.map((i) => num(i.change?.h24)).filter((v) => v !== 0).sort((a, b) => a - b);
    const med = changes.length ? changes[Math.floor(changes.length / 2)] : 0;
    $('#stTotal').textContent = nf.format(total);
    $('#stNew').textContent = fresh ? `+${nf.format(fresh)} baru < 5 mnt` : 'tidak ada pool baru';
    $('#stLiq').textContent = fmtUsd(liq);
    $('#stAge').textContent = total ? fmtAge(age) : '–';
    $('#stChain').textContent = CHAIN_NAME[state.chain] || state.chain;
    const chg = $('#stChange');
    chg.textContent = changes.length ? pct(med) : '–';
    chg.style.color = med > 0 ? 'var(--green)' : med < 0 ? 'var(--red)' : 'var(--text)';
  }

  function renderGrid(items) {
    const grid = $('#grid');
    $$('.sk', grid).forEach((el) => el.remove());
    if (!items.length) return;
    const seen = new Set();
    for (const item of items) {
      const node = ensureCard(item);
      grid.appendChild(node.el);
      updateCard(node, item);
      seen.add(node.el);
    }
    for (const [pool, node] of [...state.cards]) {
      if (!seen.has(node.el)) { node.el.remove(); state.cards.delete(pool); }
    }
  }

  function ensureCard(item) {
    const existing = state.cards.get(item.pool);
    if (existing) return existing;
    const el = document.createElement('article');
    el.className = 'card';
    const hue = hash(item.token || item.symbol) % 360;
    el.innerHTML = `
      <div class="card-head">
        <div class="avatar" style="background:linear-gradient(135deg,hsl(${hue} 70% 24%),hsl(${(hue + 70) % 360} 70% 15%))">
          <span class="ini">${esc((item.symbol || '?').slice(0, 2).toUpperCase())}</span>
          <img alt="" loading="lazy" referrerpolicy="no-referrer">
        </div>
        <div class="ident">
          <div class="top"><span class="sym"></span><span class="age"></span></div>
          <div class="name"></div>
        </div>
        <div class="chg"></div>
      </div>
      <div class="spark"><canvas></canvas></div>
      <div class="metrics">
        <div class="metric"><span>Mcap</span><b data-m="mcap"></b></div>
        <div class="metric"><span>Likuiditas</span><b data-m="liq"></b></div>
        <div class="metric"><span>Harga</span><b data-m="price"></b></div>
      </div>
      <div class="badges"></div>
      <div class="card-foot">
        <span class="chip" data-m="chain"></span>
        <span data-m="meta"></span>
        <span class="mini" data-m="socials"></span>
      </div>`;
    const refs = {
      el,
      img: $('img', el),
      ini: $('.ini', el),
      sym: $('.sym', el),
      name: $('.name', el),
      age: $('.age', el),
      chg: $('.chg', el),
      canvas: $('canvas', el),
      mcap: $('[data-m="mcap"]', el),
      liq: $('[data-m="liq"]', el),
      price: $('[data-m="price"]', el),
      chain: $('[data-m="chain"]', el),
      meta: $('[data-m="meta"]', el),
      socials: $('[data-m="socials"]', el),
      badges: $('.badges', el),
      sig: '', imgSig: '',
    };
    refs.img.onerror = () => { refs.img.style.display = 'none'; refs.ini.style.display = 'grid'; };
    refs.img.onload = () => { refs.img.style.display = 'block'; refs.ini.style.display = 'none'; };
    el.onclick = () => openDrawer(item);
    state.cards.set(item.pool, refs);
    return refs;
  }

  function updateCard(node, item) {
    const it = item;
    node.el.dataset.pool = it.pool;
    node.sym.textContent = it.symbol || '?';
    node.name.textContent = it.name || shortAddr(it.token);
    node.age.textContent = ageText(it);
    node.age.title = it.created_ts ? new Date(it.created_ts * 1000).toLocaleString('id-ID') : '';
    const chg = num(it.change?.h24);
    node.chg.textContent = pct(chg);
    node.chg.className = 'chg ' + (chg > 0 ? 'up' : chg < 0 ? 'down' : 'flat');

    node.mcap.textContent = fmtUsd(it.mcap_usd || it.fdv_usd);
    node.liq.textContent = fmtUsd(it.liquidity_usd);
    node.price.textContent = fmtPrice(it.price_usd);

    const holders = it.holders === null || it.holders === undefined ? null : Number(it.holders);
    const tax = (it.buy_tax === null || it.buy_tax === undefined) && (it.sell_tax === null || it.sell_tax === undefined)
      ? null : `${it.buy_tax ?? '?'}/${it.sell_tax ?? '?'}%`;
    node.meta.textContent = [holders !== null ? fmtNum(holders) + ' holder' : null, tax ? 'tax ' + tax : null]
      .filter(Boolean).join(' · ') || (it.dex || '');
    node.chain.textContent = CHAIN_LABEL[it.chain] || it.chain;

    const badges = [];
    if (it.honeypot) badges.push('<span class="chip bad">honeypot</span>');
    else if (num(it.buy_tax) >= 10 || num(it.sell_tax) >= 10) badges.push(`<span class="chip warn">tax ${esc(String(Math.max(num(it.buy_tax), num(it.sell_tax))))}%</span>`);
    if (it.verified === true) badges.push('<span class="chip ok">verified</span>');
    if (it.lp_burned_pct !== null && it.lp_burned_pct !== undefined && num(it.lp_burned_pct) < 50 && num(it.reserve_quote) > 0) {
      badges.push('<span class="chip warn">LP low</span>');
    }
    node.badges.innerHTML = badges.join('');

    const socials = [];
    (it.websites || []).slice(0, 1).forEach((u) => socials.push(
      `<a class="sicon" href="${esc(u)}" target="_blank" rel="noopener" title="Website">W</a>`));
    (it.socials || []).slice(0, 2).forEach((s) => {
      const key = String(s.type || '').toLowerCase();
      socials.push(`<a class="sicon" href="${esc(s.url)}" target="_blank" rel="noopener" title="${esc(s.type || 'social')}">${esc(SOCIAL_LABEL[key] || '↗')}</a>`);
    });
    node.socials.innerHTML = socials.join('');
    $$('a', node.socials).forEach((a) => a.addEventListener('click', (ev) => ev.stopPropagation()));

    if (it.image) {
      if (node.imgSig !== it.image) {
        node.imgSig = it.image;
        node.img.src = it.image;
        node.img.style.display = 'block';
      }
    } else {
      node.img.style.display = 'none';
      node.ini.style.display = 'grid';
    }

    const spark = (it.spark || []).map((p) => p.price);
    const sig = it.pool + ':' + spark.length + ':' + (spark[spark.length - 1] || 0);
    const dpr = window.devicePixelRatio || 1;
    const sizeStale = node.canvas.width !== Math.round((node.canvas.clientWidth || 0) * dpr);
    if (node.sig !== sig || sizeStale) {
      node.sig = sig;
      drawSpark(node.canvas, spark, spark.length > 1 && spark[spark.length - 1] >= spark[0]);
    }
  }

  /* ----------------------------------------------------------- sparkline */
  function drawSpark(canvas, prices, up) {
    const dpr = window.devicePixelRatio || 1;
    const w = Math.max(60, canvas.clientWidth || 200);
    const h = 44;
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    const color = up ? '#34d399' : '#fb7185';
    if (!prices || prices.length < 2) {
      ctx.strokeStyle = '#1c2637';
      ctx.setLineDash([3, 4]);
      ctx.beginPath(); ctx.moveTo(0, h / 2); ctx.lineTo(w, h / 2); ctx.stroke();
      return;
    }
    const min = Math.min(...prices);
    const max = Math.max(...prices);
    const span = max - min || max * 0.01 || 1;
    const x = (i) => (i / (prices.length - 1)) * w;
    const y = (p) => h - 6 - ((p - min) / span) * (h - 12);
    ctx.beginPath();
    prices.forEach((p, i) => (i ? ctx.lineTo(x(i), y(p)) : ctx.moveTo(x(i), y(p))));
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.5;
    ctx.lineJoin = 'round';
    ctx.stroke();
    ctx.lineTo(w, h); ctx.lineTo(0, h); ctx.closePath();
    const grad = ctx.createLinearGradient(0, 0, 0, h);
    grad.addColorStop(0, up ? 'rgba(52,211,153,.22)' : 'rgba(251,113,133,.22)');
    grad.addColorStop(1, 'rgba(0,0,0,0)');
    ctx.fillStyle = grad;
    ctx.fill();
  }

  /* ---------------------------------------------------------------- list */
  function renderList(items) {
    const box = $('#list');
    if (!items.length) { box.innerHTML = ''; return; }
    const rows = items.map((it) => {
      const chg = num(it.change?.h24);
      const cls = chg > 0 ? 'up' : chg < 0 ? 'down' : 'flat';
      const tax = `${it.buy_tax ?? '?'}/${it.sell_tax ?? '?'}%`;
      return `<div class="lrow" data-pool="${esc(it.pool)}">
        <div class="ltok">
          <div class="avatar"><span class="ini">${esc((it.symbol || '?').slice(0, 2).toUpperCase())}</span></div>
          <div style="min-width:0">
            <div class="lsym">${esc(it.symbol || '?')} <span class="age">${ageText(it)}</span></div>
            <div class="lname">${esc(it.name || shortAddr(it.token))} · ${esc(CHAIN_LABEL[it.chain] || it.chain)}</div>
          </div>
        </div>
        <div class="lcell num">${fmtPrice(it.price_usd)}</div>
        <div class="lcell num" style="color:${chg > 0 ? 'var(--green)' : chg < 0 ? 'var(--red)' : 'var(--muted)'}">${pct(chg)}</div>
        <div class="lcell num">${fmtUsd(it.liquidity_usd)}</div>
        <div class="lcell num hide-sm">${fmtUsd(it.mcap_usd || it.fdv_usd)}</div>
        <div class="lcell num hide-md">${fmtUsd(it.volume?.h24)}</div>
        <div class="lcell num hide-sm">${it.holders === null || it.holders === undefined ? '–' : fmtNum(it.holders)}</div>
        <div class="lcell num hide-md">${tax}</div>
      </div>`;
    }).join('');
    box.innerHTML = `<div class="lhead">
        <div>Token</div><div class="lcell num">Harga</div><div class="lcell num">24 j</div>
        <div class="lcell num">Likuiditas</div><div class="lcell num hide-sm">Mcap</div>
        <div class="lcell num hide-md">Volume</div><div class="lcell num hide-sm">Holder</div>
        <div class="lcell num hide-md">Pajak</div>
      </div>${rows}`;
  }

  /* -------------------------------------------------------------- drawer */
  function openDrawer(item) {
    state.selected = item;
    state.chart.tf = 'minute';
    state.chart.agg = 1;
    renderDrawer(item);
    $('#drawer').classList.add('open');
    $('#drawer').setAttribute('aria-hidden', 'false');
    document.body.style.overflow = 'hidden';
    loadChart();
  }

  function closeDrawer() {
    $('#drawer').classList.remove('open');
    $('#drawer').setAttribute('aria-hidden', 'true');
    document.body.style.overflow = '';
    state.selected = null;
  }

  function renderDrawer(it) {
    const chg = num(it.change?.h24);
    const chgCls = chg > 0 ? 'up' : chg < 0 ? 'down' : 'flat';
    const hue = hash(it.token || it.symbol) % 360;
    const cp = $('#drawerBody');
    const taxText = (it.buy_tax === null || it.buy_tax === undefined) && (it.sell_tax === null || it.sell_tax === undefined)
      ? '–' : `${it.buy_tax ?? '?'}% / ${it.sell_tax ?? '?'}%`;
    const warn = [];
    if (it.honeypot) warn.push('Terdeteksi honeypot oleh GoPlus.');
    if (num(it.buy_tax) >= 10 || num(it.sell_tax) >= 10) warn.push('Pajak beli/jual tinggi (≥10%).');
    if (it.verified === false) warn.push('Contract belum terverifikasi.');
    if (it.lp_burned_pct !== null && it.lp_burned_pct !== undefined && num(it.lp_burned_pct) < 50 && num(it.reserve_quote) > 0) warn.push('LP belum dibakar (<50%).');

    const socials = [];
    (it.websites || []).forEach((u) => socials.push(`<a class="btn" href="${esc(u)}" target="_blank" rel="noopener">Website ↗</a>`));
    (it.socials || []).forEach((s) => socials.push(`<a class="btn" href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.type || 'social')} ↗</a>`));

    cp.innerHTML = `
      <div class="dhead">
        <div class="avatar" style="background:linear-gradient(135deg,hsl(${hue} 70% 24%),hsl(${(hue + 70) % 360} 70% 15%))">
          <span class="ini">${esc((it.symbol || '?').slice(0, 2).toUpperCase())}</span>
          ${it.image ? `<img src="${esc(it.image)}" alt="" referrerpolicy="no-referrer">` : ''}
        </div>
        <div style="min-width:0">
          <div class="dsym">${esc(it.symbol || '?')} <span class="chg ${chgCls}" style="font-size:11px;vertical-align:2px">${pct(chg)}</span></div>
          <div class="dname">${esc(it.name || '')} · ${esc(it.dex_name || it.dex)}</div>
        </div>
      </div>
      <div class="dsub">
        <span class="chip">${esc(CHAIN_LABEL[it.chain] || it.chain)}</span>
        <span>${ageText(it)}${num(it.created_ts) > 0 ? ' lalu' : ''}</span><span>·</span>
        <span>${esc(String(it.kind || '').toUpperCase())}</span>
        ${it.quote_symbol ? `<span>· quote ${esc(it.quote_symbol)}</span>` : ''}
      </div>
      <div class="actions">
        <button class="btn primary" id="copyCa">Salin CA</button>
        <a class="btn" href="${tokenLink(it)}" target="_blank" rel="noopener">Explorer ↗</a>
        <a class="btn" href="${dsLink(it)}" target="_blank" rel="noopener">DexScreener ↗</a>
        ${socials.slice(0, 3).join('')}
      </div>
      ${warn.length ? `<div class="sec">⚠ ${esc(warn.join(' '))}</div>` : ''}
      <div class="chartbox">
        <div class="chart-top">
          <span class="price" id="chartPrice">${fmtPrice(it.price_usd)}</span>
          <span class="chg ${chgCls}" id="chartChg">${pct(chg)}</span>
          <div class="tfs" id="tfs">
            <button class="tf active" data-tf="minute" data-agg="1">1m</button>
            <button class="tf" data-tf="minute" data-agg="5">5m</button>
            <button class="tf" data-tf="minute" data-agg="15">15m</button>
            <button class="tf" data-tf="hour" data-agg="1">1j</button>
            <button class="tf" data-tf="hour" data-agg="4">4j</button>
            <button class="tf" data-tf="day" data-agg="1">1h</button>
          </div>
        </div>
        <canvas id="chart" height="250"></canvas>
        <div class="chart-note"><span id="chartSource">memuat grafik…</span><span id="chartHint">geser untuk detail candle</span></div>
      </div>
      <div class="dgrid">
        ${kv('Likuiditas', fmtUsd(it.liquidity_usd) + (it.liquidity_native ? ` · ${fmtNum(Number(it.liquidity_native).toFixed(4))} ${esc(it.quote_symbol || '')}` : ''))}
        ${kv('FDV / Mcap', `${fmtUsd(it.fdv_usd)} / ${fmtUsd(it.mcap_usd || it.fdv_usd)}`)}
        ${kv('Harga', `${fmtPrice(it.price_usd)}${it.price_native ? ` · ${Number(it.price_native).toPrecision(6)} ${esc(it.quote_symbol || '')}` : ''}`)}
        ${kv('Total supply', it.total_supply ? fmtNum(it.total_supply) : '–')}
        ${kv('Holder', it.holders === null || it.holders === undefined ? '–' : fmtNum(it.holders) + (it.top10_pct !== null && it.top10_pct !== undefined ? ` · top10 ${num(it.top10_pct).toFixed(1)}%` : ''))}
        ${kv('Pajak beli / jual', taxText)}
        ${kv('Honeypot', it.honeypot === null || it.honeypot === undefined ? '–' : (it.honeypot ? 'YA' : 'tidak'))}
        ${kv('Verified', it.verified === null || it.verified === undefined ? '–' : (it.verified ? 'ya' : 'tidak'))}
        ${kv('LP burned', it.lp_burned_pct === null || it.lp_burned_pct === undefined ? '–' : num(it.lp_burned_pct).toFixed(1) + '%')}
        ${kv('Fee pool', it.fee_tier ? it.fee_tier + '%' : '–')}
        ${kv('Reserve', it.reserve_quote ? `${fmtNum(Number(it.reserve_quote).toFixed(4))} ${esc(it.quote_symbol || '')}` : '–')}
        ${kv('Block', it.created_block ? nf.format(it.created_block) : '–')}
        ${kv('Owner', it.owner ? `<a href="${(EXPLORER[it.chain] || '' )}/address/${esc(it.owner)}" target="_blank" rel="noopener">${shortAddr(it.owner)}</a>` : '–')}
        ${kv('Creator', it.creator ? `<a href="${(EXPLORER[it.chain] || '' )}/address/${esc(it.creator)}" target="_blank" rel="noopener">${shortAddr(it.creator)}</a>` : '–')}
        ${kv('Contract', `<a href="${tokenLink(it)}" target="_blank" rel="noopener">${shortAddr(it.token)}</a>`)}
        ${kv('Pool', `<a href="${poolLink(it)}" target="_blank" rel="noopener">${shortAddr(it.pool)}</a>`)}
      </div>
      ${(it.notes && it.notes.length) ? `<div class="tagline">Catatan: ${esc(it.notes.join(' · '))}</div>` : ''}
      ${(it.sources && it.sources.length) ? `<div class="tagline">Sumber data: ${esc(it.sources.join(', '))}</div>` : ''}
    `;

    $('#copyCa').onclick = () => copyText(it.token);
    $$('#tfs .tf').forEach((btn) => {
      btn.onclick = () => {
        $$('#tfs .tf').forEach((b) => b.classList.remove('active'));
        btn.classList.add('active');
        state.chart.tf = btn.dataset.tf;
        state.chart.agg = Number(btn.dataset.agg);
        loadChart();
      };
    });
    $$('#drawerBody img').forEach((img) => {
      img.onerror = () => { img.style.display = 'none'; };
    });
  }

  function kv(label, value) {
    if (value === null || value === undefined || value === '') return '';
    return `<div class="kv"><dt>${esc(label)}</dt><dd>${value}</dd></div>`;
  }

  /* --------------------------------------------------------------- chart */
  async function loadChart() {
    if (!state.selected) return;
    const it = state.selected;
    const source = $('#chartSource');
    if (source) source.textContent = 'memuat grafik…';
    try {
      const params = new URLSearchParams({
        chain: state.chain, pool: it.pool, tf: state.chart.tf, agg: String(state.chart.agg),
      });
      const res = await fetch('/api/chart?' + params, { cache: 'no-store' });
      const data = await res.json();
      state.chart.candles = data.candles || [];
      state.chart.source = data.source || '';
      const src = $('#chartSource');
      if (src) {
        src.textContent = `${state.chart.candles.length} candle · ${data.source || ''} · ${state.chart.tf}/${state.chart.agg}`;
      }
      drawChart();
    } catch (err) {
      const src = $('#chartSource');
      if (src) src.textContent = 'grafik tidak tersedia';
    }
  }

  function drawChart() {
    const canvas = $('#chart');
    if (!canvas) return;
    const candles = state.chart.candles || [];
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth || 560;
    const h = 250;
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);

    if (!candles.length) {
      ctx.fillStyle = '#4d5a70';
      ctx.font = '12px ui-sans-serif,system-ui';
      ctx.textAlign = 'center';
      ctx.fillText('Belum ada data candle untuk pool ini', w / 2, h / 2);
      return;
    }

    const padL = 6, padR = 58, padT = 10, padB = 22, volH = 42;
    const plotH = h - padT - padB - volH;
    const lows = candles.map((c) => c.low);
    const highs = candles.map((c) => c.high);
    const min = Math.min(...lows);
    const max = Math.max(...highs);
    const span = max - min || max * 0.01 || 1;
    const y = (p) => padT + plotH - ((p - min) / span) * plotH;
    const step = (w - padL - padR) / candles.length;
    const bw = Math.max(1, Math.min(step * 0.62, 12));
    const maxVol = Math.max(...candles.map((c) => c.volume || 0), 1);
    const volY = (v) => h - padB - ((v || 0) / maxVol) * volH;
    const volBase = h - padB;

    ctx.strokeStyle = '#141b27';
    ctx.lineWidth = 1;
    ctx.font = '10px ui-monospace,SFMono-Regular,Menlo,monospace';
    ctx.fillStyle = '#4d5a70';
    for (let i = 0; i <= 4; i++) {
      const gy = padT + (plotH / 4) * i;
      ctx.beginPath(); ctx.moveTo(padL, gy); ctx.lineTo(w - padR, gy); ctx.stroke();
      const price = max - (span / 4) * i;
      ctx.textAlign = 'left';
      ctx.fillText(fmtPrice(price).replace('$', ''), w - padR + 6, gy + 3);
    }

    candles.forEach((c, i) => {
      const x = padL + step * i + step / 2;
      const up = c.close >= c.open;
      const color = up ? '#34d399' : '#fb7185';
      ctx.strokeStyle = color;
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.moveTo(x, y(c.high));
      ctx.lineTo(x, y(c.low));
      ctx.stroke();
      const top = y(Math.max(c.open, c.close));
      const bottom = y(Math.min(c.open, c.close));
      const bodyH = Math.max(1, bottom - top);
      ctx.fillRect(x - bw / 2, top, bw, bodyH);
      ctx.globalAlpha = 0.35;
      ctx.fillRect(x - bw / 2, volY(c.volume), bw, volBase - volY(c.volume));
      ctx.globalAlpha = 1;
    });

    const count = Math.min(4, candles.length);
    ctx.fillStyle = '#4d5a70';
    ctx.textAlign = 'center';
    for (let i = 0; i < count; i++) {
      const idx = Math.floor((candles.length - 1) * (i / Math.max(1, count - 1)));
      const c = candles[idx];
      const x = padL + step * idx + step / 2;
      const d = new Date(c.ts * 1000);
      const label = state.chart.tf === 'day'
        ? d.toLocaleDateString('id-ID', { day: '2-digit', month: 'short' })
        : d.toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit' });
      ctx.fillText(label, Math.max(padL, Math.min(x, w - padR)), h - 6);
    }

    state.chart.layout = { w, h, padL, padR, padT, padB, step, candles, y, min, max, span };

    const tip = $('#chartTip');
    if (!tip) {
      const el = document.createElement('div');
      el.id = 'chartTip';
      document.body.appendChild(el);
    }
    const chart = canvas;
    chart.onmousemove = (ev) => {
      const layout = state.chart.layout;
      if (!layout) return;
      const rect = chart.getBoundingClientRect();
      const mx = ev.clientX - rect.left;
      const idx = Math.max(0, Math.min(layout.candles.length - 1, Math.floor((mx - layout.padL) / layout.step)));
      const c = layout.candles[idx];
      const tipEl = $('#chartTip');
      const d = new Date(c.ts * 1000);
      tipEl.innerHTML = `<b>${d.toLocaleString('id-ID')}</b><br>
        O ${fmtPrice(c.open)} · H ${fmtPrice(c.high)}<br>
        L ${fmtPrice(c.low)} · C ${fmtPrice(c.close)}<br>
        Vol ${fmtUsd(c.volume)}`;
      tipEl.style.display = 'block';
      const x = ev.clientX + 14;
      const yPos = Math.min(ev.clientY + 14, window.innerHeight - 90);
      tipEl.style.left = Math.min(x, window.innerWidth - 220) + 'px';
      tipEl.style.top = yPos + 'px';
    };
    chart.onmouseleave = () => {
      const tipEl = $('#chartTip');
      if (tipEl) tipEl.style.display = 'none';
    };
  }

  /* -------------------------------------------------------------- events */
  function bind() {
    $('#q').addEventListener('input', (ev) => {
      state.q = ev.target.value;
      render();
    });
    $('#minLiq').addEventListener('change', (ev) => {
      state.minLiq = Number(ev.target.value);
      localStorage.setItem('dw.minLiq', state.minLiq);
      render();
    });
    $('#hideHp').addEventListener('change', (ev) => {
      state.hideHp = ev.target.checked;
      localStorage.setItem('dw.hideHp', state.hideHp ? '1' : '0');
      render();
    });
    $('#onlyVerified').addEventListener('change', (ev) => {
      state.onlyVerified = ev.target.checked;
      localStorage.setItem('dw.verified', state.onlyVerified ? '1' : '0');
      render();
    });
    $('#tabs').addEventListener('click', (ev) => {
      const btn = ev.target.closest('.tab');
      if (!btn) return;
      $$('#tabs .tab').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      state.sort = btn.dataset.sort;
      localStorage.setItem('dw.sort', state.sort);
      render();
    });
    $('#view').addEventListener('click', (ev) => {
      const btn = ev.target.closest('button');
      if (!btn) return;
      $$('#view button').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      state.view = btn.dataset.view;
      localStorage.setItem('dw.view', state.view);
      render();
    });
    $('#autoBtn').addEventListener('click', () => {
      state.auto = !state.auto;
      localStorage.setItem('dw.auto', state.auto ? '1' : '0');
      $('#autoBtn').classList.toggle('on', state.auto);
    });
    $('#reloadBtn').addEventListener('click', loadFeed);
    $('#closeDrawer').addEventListener('click', closeDrawer);
    $('#drawer').addEventListener('click', (ev) => {
      if (ev.target === $('#drawer')) closeDrawer();
    });
    $('#list').addEventListener('click', (ev) => {
      const row = ev.target.closest('.lrow');
      if (!row) return;
      const item = state.items.find((i) => i.pool === row.dataset.pool);
      if (item) openDrawer(item);
    });
    document.addEventListener('keydown', (ev) => {
      if (ev.key === 'Escape') closeDrawer();
      if (ev.key === '/' && document.activeElement !== $('#q')) {
        ev.preventDefault();
        $('#q').focus();
      }
    });
    window.addEventListener('resize', () => {
      clearTimeout(bind._t);
      bind._t = setTimeout(() => {
        if ($('#drawer').classList.contains('open')) drawChart();
        for (const [, node] of state.cards) {
          const spark = (state.items.find((i) => i.pool === node.el.dataset.pool)?.spark || []).map((p) => p.price);
          drawSpark(node.canvas, spark, spark.length > 1 && spark[spark.length - 1] >= spark[0]);
        }
      }, 200);
    });
  }

  function restoreControls() {
    $('#minLiq').value = String(state.minLiq);
    $('#hideHp').checked = state.hideHp;
    $('#onlyVerified').checked = state.onlyVerified;
    $('#autoBtn').classList.toggle('on', state.auto);
    $$('#tabs .tab').forEach((b) => b.classList.toggle('active', b.dataset.sort === state.sort));
    $$('#view button').forEach((b) => b.classList.toggle('active', b.dataset.view === state.view));
  }

  /* ------------------------------------------------------------------ go */
  function init() {
    restoreControls();
    renderChains();
    bind();
    $('#grid').innerHTML = Array.from({ length: 8 }, () => '<div class="sk"></div>').join('');
    loadFeed();
    setInterval(() => {
      if (state.auto && !document.hidden) loadFeed();
    }, 5000);
    setInterval(() => {
      if (!$('#drawer').classList.contains('open')) return;
      if (state.selected) {
        const fresh = state.items.find((i) => i.pool === state.selected.pool);
        if (fresh) {
          const p = $('#chartPrice');
          if (p) p.textContent = fmtPrice(fresh.price_usd);
        }
      }
    }, 5000);
  }

  document.addEventListener('DOMContentLoaded', init);
})();
