(() => {
  'use strict';
  const $ = (selector, root = document) => root.querySelector(selector);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const currency = value => Number(value || 0).toLocaleString('zh-CN', {minimumFractionDigits:2, maximumFractionDigits:2});
  const time = value => new Date(value).toLocaleString('zh-CN', {timeZone:'Asia/Shanghai', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', hour12:false});
  const labels = {dashboard:['经营概览','BUSINESS OVERVIEW','掌握订单、库存与收款，开始今天的经营。'],orders:['订单中心','ORDER MANAGEMENT','汇集各个渠道的订单，统一占用库存与发货。'],products:['商品资料','PRODUCT CATALOG','统一商品编码与售价，库存按门店分别管理。'],inventory:['库存管理','INVENTORY','实物、占用与可用库存，清楚区分每一件商品。'],channels:['门店与渠道','STORES & CHANNELS','管理履约门店与订单来源。'],payments:['收款与退款','PAYMENT RECORDS','登记实际发生的收退款，并保留对应凭证。'],movements:['库存流水','STOCK MOVEMENTS','查看每次入库、占用、发货与退货的记录。'],audit:['操作记录','ACTIVITY LOG','追溯关键业务操作与执行人。']};
  let state = null, page = 'dashboard', filters = {q:'',status:'all'}, toastTimer;
  const content = $('#page-content'), dialog = $('#app-dialog');

  function toast(message) { $('#toast').textContent = message; $('#toast').classList.add('visible'); clearTimeout(toastTimer); toastTimer=setTimeout(()=>$('#toast').classList.remove('visible'),3500); }
  async function api(path, data, key) {
    const csrf = document.cookie.split('; ').find(c=>c.startsWith('csrftoken='))?.slice(10);
    const response = await fetch(path, data === undefined ? {headers:{Accept:'application/json'}} : {
      method:'POST', headers:{'Content-Type':'application/json','X-CSRFToken':csrf || '', 'Idempotency-Key':key || crypto.randomUUID()}, body:JSON.stringify(data)
    });
    if(response.status===401) { location.href='/login/'; throw new Error('登录已过期'); }
    let result;
    try { result=await response.json(); } catch { throw new Error('服务暂不可用，请稍后重试。'); }
    if(!response.ok) throw new Error(result.error || '操作失败，请刷新后重试');
    return result;
  }
  async function load() {
    const selected=$('#store-filter').value;
    state=await api('/api/state/' + (selected ? '?store='+encodeURIComponent(selected) : ''));
    $('#store-filter').innerHTML='<option value="">全部门店</option>'+state.stores.map(s=>`<option value="${s.id}">${esc(s.name)}</option>`).join('');
    $('#store-filter').value=selected;
    $('#business-date').textContent=state.today.replaceAll('-',' / ');
    $('#nav-pending').textContent=state.stats.pending || '';
    render();
  }
  function navigate(next) {
    if(!labels[next]) return;
    page=next; filters={q:'',status:'all'}; history.replaceState(null,'','#'+page);
    $('.sidebar').classList.remove('open'); render();
  }
  function status(o) { return `<span class="status ${esc(o.status)}">${esc(o.status_label)}</span>`; }
  function blank(message='暂时没有记录') { return `<div class="empty">${esc(message)}</div>`; }
  function table(head, rows) { return rows.length ? `<div class="table-wrap"><table class="data-table"><thead><tr>${head.map(h=>`<th>${h}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table></div>` : blank(filters.q ? '没有匹配的记录，请调整搜索条件。' : '暂无记录，可以从右上角开始新增。'); }
  function searchbox(placeholder) { return `<input class="search-input" id="table-search" type="search" aria-label="${placeholder}" placeholder="⌕  ${placeholder}" value="${esc(filters.q)}">`; }
  function matches(...values) { return values.join(' ').toLowerCase().includes(filters.q.trim().toLowerCase()); }
  function exportButton(resource,label='导出 CSV') { return `<a class="button small" href="/api/export/${resource}/">↓ ${label}</a>`; }
  function footer(total,limit=200) { return `<div class="table-footnote"><span>显示 ${Math.min(total,limit)} 条${total>=limit?' · 列表最多展示最近 '+limit+' 条':''}</span><span>导出文件包含全部门店的完整记录</span></div>`; }
  function orderRows(orders) { return orders.map(o=>`<tr><td><button class="text-button order-number" data-order="${o.id}">${esc(o.number)}</button><small>${time(o.created_at)}</small></td><td>${esc(o.customer)}<small>${esc(o.items.map(i=>i.name).join('、'))}</small></td><td>${esc(o.channel)}<small>${esc(o.store)}</small></td><td class="number">¥${currency(o.total)}<small>${o.refunded_total!=='0.00'?'已退 ¥'+currency(o.refunded_total):o.paid?'已登记收款':'待收款'}</small></td><td>${status(o)}</td><td class="align-right"><button class="text-button" data-order="${o.id}">查看 →</button></td></tr>`); }
  function renderChart() {
    const panelWidth=window.innerWidth>800 ? (content.clientWidth-23)*1.9/2.9 : content.clientWidth;
    const width=Math.max(280,Math.round(panelWidth)-32),height=214,left=49,right=20,top=12,bottom=35,items=state.trend;
    const max=Math.max(100,...items.flatMap(v=>[+v.received,+v.refunded]));
    const ceiling=Math.ceil(max/4/100)*400;
    const x=i=>left+i*(width-left-right)/6, y=v=>top+(ceiling-v)/ceiling*(height-top-bottom);
    let svg=`<svg class="trend-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="近七天每日收款与退款金额，单位人民币"><title>近七天人工登记的收款与退款</title>`;
    for(let i=0;i<=4;i++){const v=ceiling*i/4;svg+=`<line x1="${left}" x2="${width-right}" y1="${y(v)}" y2="${y(v)}" stroke="#eff0f5"/><text x="${left-10}" y="${y(v)+3}" text-anchor="end">${v>=1000?(v/1000).toFixed(v%1000?1:0)+'k':v}</text>`;}
    const path=field=>items.map((v,i)=>`${i?'L':'M'}${x(i)},${y(+v[field])}`).join(' ');
    svg+=`<defs><linearGradient id="chart-area" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stop-color="#8071dc" stop-opacity=".15"/><stop offset="100%" stop-color="#8071dc" stop-opacity="0"/></linearGradient></defs><path d="${path('received')} L${x(6)},${y(0)} L${x(0)},${y(0)} Z" fill="url(#chart-area)"/><path d="${path('received')}" fill="none" stroke="#8273db" stroke-width="2.2" stroke-linejoin="round"/><path d="${path('refunded')}" fill="none" stroke="#bdc1cf" stroke-width="1.7" stroke-dasharray="4 4"/>`;
    items.forEach((v,i)=>{svg+=`<circle cx="${x(i)}" cy="${y(+v.received)}" r="3" fill="#fff" stroke="#8273db" stroke-width="1.6"><title>${v.date} 收款 ¥${currency(v.received)} · 退款 ¥${currency(v.refunded)}</title></circle><text x="${x(i)}" y="${height-12}" text-anchor="middle">${v.date.slice(5).replace('-','/')}</text>`;});
    return svg+'</svg>';
  }
  function dashboard() {
    const stats=state.stats, max=Math.max(1,...state.channels.map(c=>Math.abs(+c.net)));
    return `<section class="kpi-strip" aria-label="今日经营指标"><div class="kpi"><div class="kpi-label">今日净收款 <span class="kpi-symbol">¥</span></div><div class="kpi-value"><span class="currency">¥</span>${currency(stats.today_net)}</div><div class="kpi-detail">收款 ${currency(stats.today_received)} · 退款 ${currency(stats.today_refunded)}</div></div><div class="kpi"><div class="kpi-label">待发货订单 <span class="kpi-symbol">≡</span></div><div class="kpi-value">${stats.pending}<span style="font-size:11px;color:var(--muted);margin-left:6px">单</span></div><button class="text-button" data-nav="orders">前往处理 →</button></div><div class="kpi"><div class="kpi-label">库存预警 <span class="kpi-symbol">◇</span></div><div class="kpi-value">${stats.low_stock}<span style="font-size:11px;color:var(--muted);margin-left:6px">项</span></div><div class="kpi-detail">按商品 × 门店统计</div></div><div class="kpi"><div class="kpi-label">可用库存总数 <span class="kpi-symbol">▤</span></div><div class="kpi-value">${stats.available.toLocaleString()}</div><div class="kpi-detail">已扣除订单占用数量</div></div></section>
    <div class="overview-grid"><section class="panel"><div class="panel-head"><div class="panel-title"><h2>收退款趋势</h2><small>近 7 天 · 人民币 CNY</small></div><div class="chart-legend"><span><i class="legend-dot"></i>收款</span><span><i class="legend-dot refund"></i>退款</span></div></div>${renderChart()}</section><section class="panel"><div class="panel-head"><div class="panel-title"><h2>渠道经营</h2><small>累计净收款 · 当前门店范围</small></div><button class="text-button" data-nav="channels">管理 →</button></div><div class="channel-list">${state.channels.map(c=>`<div class="channel-row"><span class="channel-initial">${esc(c.name[0])}</span><div><div class="channel-name">${esc(c.name)}</div><div class="channel-note">${c.orders} 笔订单 · ${c.kind==='retail'?'线下门店':'线上渠道'}</div></div><div><div class="channel-money">¥${currency(c.net)}</div><div class="channel-bar"><i style="width:${Math.abs(+c.net)/max*100}%"></i></div></div></div>`).join('') || blank('还没有渠道')}</div></section></div>
    <section class="panel section-spacer"><div class="panel-head"><div class="panel-title"><h2>最近订单</h2><small>最新业务动态，按创建时间排列</small></div><button class="text-button" data-nav="orders">查看全部订单 →</button></div>${table(['订单编号','客户 / 商品','渠道 / 门店','订单金额','订单状态',''],orderRows(state.orders.slice(0,6)))}</section>`;
  }
  function orders() {
    const tabs=[['all','全部订单'],['pending','待发货'],['shipped','已发货'],['partial_return','部分退货'],['returned','已退货'],['canceled','已取消']];
    const rows=state.orders.filter(o=>(filters.status==='all'||o.status===filters.status)&&matches(o.number,o.external_id,o.customer,o.channel,...o.items.map(i=>i.name+' '+i.sku)));
    return `<section class="panel"><div class="filter-tabs">${tabs.map(([key,label])=>`<button class="filter-tab ${filters.status===key?'active':''}" data-status="${key}">${label}</button>`).join('')}</div><div class="toolbar">${searchbox('搜索订单号、客户或商品')}<div class="toolbar-group">${state.user.can_operate?'<button class="button small" data-action="import">↑ 导入渠道订单</button>':''}${exportButton('orders')}</div></div>${table(['订单编号','客户 / 商品','渠道 / 门店','订单金额','订单状态',''],orderRows(rows))}${footer(rows.length)}</section>`;
  }
  function products() {
    const rows=state.products.filter(p=>matches(p.sku,p.name,p.category)).map(p=>{const stocks=state.inventory.filter(i=>i.product_id===p.id);const count=stocks.reduce((s,i)=>s+i.available,0);return `<tr><td class="sku">${esc(p.sku)}</td><td>${esc(p.name)}</td><td>${esc(p.category)}</td><td class="number">¥${currency(p.price)}</td><td class="number">${count.toLocaleString()}</td><td>${p.reorder_level}</td><td><div class="toolbar-group">${state.user.can_manage?`<button class="text-button" data-action="product" data-product="${p.id}">编辑</button>`:''}<button class="text-button" data-action="receive" data-product="${p.id}" ${state.user.can_operate?'':'hidden'}>补货入库 →</button></div></td></tr>`;});
    return `<section class="panel"><div class="toolbar">${searchbox('搜索 SKU、名称或分类')}<span class="kpi-detail">共 ${state.products.length} 个商品 · 单一销售单位</span></div>${table(['商品编码','商品名称','分类','参考售价','可用库存','预警阈值',''],rows)}<div class="table-footnote"><span>创建订单时可调整成交单价，历史订单保留原始价格。</span></div></section>`;
  }
  function inventory() {
    const rows=state.inventory.filter(i=>matches(i.sku,i.name,i.store)&&(filters.status!=='low'||i.available<=i.reorder_level)).map(i=>`<tr><td>${esc(i.name)}<small>${esc(i.sku)}</small></td><td>${esc(i.store)}</td><td class="number">${i.on_hand}</td><td>${i.reserved}</td><td class="number ${i.available<=i.reorder_level?'lowstock':''}">${i.available}</td><td>${i.available<=i.reorder_level?'<span class="status warning">低于或等于预警线</span>':'<span class="status shipped">库存充足</span>'}</td><td class="align-right">${state.user.can_operate?`<button class="text-button" data-action="receive" data-product="${i.product_id}" data-store="${i.store_id}">补货 →</button>`:''}</td></tr>`);
    return `<section class="panel"><div class="filter-tabs"><button class="filter-tab ${filters.status==='all'?'active':''}" data-status="all">全部库存</button><button class="filter-tab ${filters.status==='low'?'active':''}" data-status="low">库存预警 ${state.stats.low_stock}</button></div><div class="toolbar">${searchbox('搜索 SKU、商品或门店')}${exportButton('inventory')}</div>${table(['商品 / SKU','门店','实物库存','订单占用','可用库存','库存状态',''],rows)}<div class="table-footnote"><span>可用 = 实物 − 占用；订单创建时占用，发货时扣实物。</span><span>${rows.length} 项库存</span></div></section>`;
  }
  function channels() {return `<div class="entity-grid"><section class="panel"><div class="panel-head"><div class="panel-title"><h2>履约门店</h2><small>${state.stores.length} 个门店 · 独立管理库存</small></div>${state.user.can_manage?'<button class="text-button" data-action="store">＋ 新建门店</button>':''}</div>${state.stores.map(s=>`<div class="entity-row"><div><h3>${esc(s.name)}</h3><p>${esc(s.code)} · ${esc(s.location||'尚未填写地址')}</p></div><span class="status shipped">使用中</span></div>`).join('')||blank('暂无门店')}</section><section class="panel"><div class="panel-head"><div class="panel-title"><h2>销售渠道</h2><small>手工录单或 CSV 导入</small></div>${state.user.can_manage?'<button class="text-button" data-action="channel">＋ 新建渠道</button>':''}</div>${state.channels.map(c=>`<div class="entity-row"><div><h3>${esc(c.name)}</h3><p>${c.kind==='retail'?'线下门店':'线上渠道'} · ${c.orders} 笔订单</p></div><span class="status">手工管理</span></div>`).join('')||blank('暂无渠道')}<div class="connection-note">当前版本支持渠道标记和 CSV 导入。自动拉单、支付回调与平台库存同步列入后续版本。</div></section></div>`;}
  function payments() {const rows=state.payments.filter(p=>matches(p.number,p.reference,p.channel)).map(p=>`<tr><td class="order-number">${esc(p.number)}</td><td>${esc(p.channel)}</td><td><span class="status ${p.kind==='receipt'?'shipped':'returned'}">${p.kind==='receipt'?'收款登记':'退款登记'}</span></td><td class="number">${p.kind==='refund'?'−':'+'} ¥${currency(p.amount)}</td><td>${esc(p.reference)}</td><td>${esc(p.actor)}</td><td>${time(p.created_at)}</td></tr>`);return `<section class="panel"><div class="toolbar">${searchbox('搜索订单、渠道或凭证')}${exportButton('payments')}</div>${table(['关联订单','来源渠道','类型','金额','收退款凭证','操作人','登记时间'],rows)}${footer(rows.length)}</section>`;}
  function movements() {const rows=state.movements.filter(m=>matches(m.sku,m.name,m.store,m.number,m.note)).map(m=>`<tr><td>${esc(m.name)}<small>${esc(m.sku)}</small></td><td>${esc(m.store)}</td><td>${esc(m.kind)}</td><td class="delta ${m.delta<0?'negative':''}">${m.delta>0?'+':''}${m.delta}</td><td>${m.reserved_delta>0?'+':''}${m.reserved_delta}</td><td>${m.on_hand_after} / ${m.reserved_after}</td><td class="order-number">${esc(m.number)}<small>${esc(m.note)}</small></td><td>${esc(m.actor)}<small>${time(m.created_at)}</small></td></tr>`);return `<section class="panel"><div class="toolbar">${searchbox('搜索商品、门店或订单')}<span class="kpi-detail">库存流水随业务生成，保留原始记录</span></div>${table(['商品 / SKU','门店','业务类型','实物变动','占用变动','变动后实物 / 占用','关联订单 / 备注','操作人 / 时间'],rows)}${footer(rows.length)}</section>`;}
  function audit() {const names={'order.create':'创建订单','order.pay':'登记收款','order.ship':'订单发货','order.cancel':'取消订单','order.refund':'退货退款','order.import':'CSV 导入','stock.receive':'补货入库','product.create':'创建商品','store.create':'创建门店','channel.create':'创建渠道'};const rows=state.audit.filter(a=>matches(a.action,a.object_ref,a.actor,names[a.action]||'')).map(a=>`<tr><td>${time(a.created_at)}</td><td>${esc(a.actor)}</td><td>${esc(names[a.action]||a.action)}</td><td class="order-number">${esc(a.object_ref)}</td><td>${esc(Object.entries(a.detail).map(([k,v])=>k+': '+v).join(' · ')||'—')}</td></tr>`);return `<section class="panel"><div class="toolbar">${searchbox('搜索操作、单号或执行人')}<span class="kpi-detail">仅管理员可见</span></div>${table(['发生时间','执行人','操作','业务对象','详情'],rows)}${footer(rows.length,100)}</section>`;}
  function render() {
    if(!state) return;
    const [title,eyebrow,description]=labels[page]; $('#page-title').textContent=title; $('#crumb-title').textContent=title; $('#page-eyebrow').textContent=eyebrow; $('#page-description').textContent=description;
    document.querySelectorAll('[data-page]').forEach(button=>{button.classList.toggle('active',button.dataset.page===page);button.setAttribute('aria-current',button.dataset.page===page?'page':'false');});
    const primary=$('#primary-action');
    const action={products:['＋ 新建商品','product',state.user.can_manage],inventory:['＋ 补货入库','receive',state.user.can_operate],channels:['＋ 新建渠道','channel',state.user.can_manage]}[page]||['＋ 新建订单','order',state.user.can_operate];
    primary.textContent=action[0];primary.dataset.action=action[1];primary.hidden=!action[2];primary.disabled=false;
    content.innerHTML=({dashboard,orders,products,inventory,channels,payments,movements,audit}[page])();
  }
  function showDialog(title,html) {$('#dialog-title').textContent=title;$('#dialog-body').innerHTML=html;dialog.showModal();}
  function closeDialog(){dialog.close();}
  function form(title,html,submit,path,build) {
    showDialog(title,`<form id="business-form" class="dialog-content"><div class="form-error" id="form-error" hidden role="alert"></div>${html}<div class="form-actions"><button type="button" class="button" data-close>取消</button><button type="submit" class="button primary">${submit}</button></div></form>`);
    let pendingPayload='',requestKey='';
    $('#business-form').addEventListener('submit',async event=>{
      event.preventDefault(); const element=event.currentTarget,button=$('button[type=submit]',element),error=$('#form-error');error.hidden=true;button.disabled=true;
      try {
        const data=await build(new FormData(element),element),payload=JSON.stringify(data);
        if(payload!==pendingPayload){pendingPayload=payload;requestKey=crypto.randomUUID();}
        const result=await api(path,data,requestKey);closeDialog();toast(result.message);await load();
      } catch(problem){error.textContent=problem.message;error.hidden=false;}finally{button.disabled=false;}
    });
  }
  function options(list,label='name',selected='') {return list.map(item=>`<option value="${item.id}" ${String(item.id)===String(selected)?'selected':''}>${esc(item[label])}</option>`).join('');}
  function storeOptions(selected) {return options(state.stores,'name',selected||$('#store-filter').value);}
  function newOrder() {
    if(!state.products.length||!state.stores.length||!state.channels.length){toast('请先创建商品、门店和渠道，再补货入库。');return;}
    form('新建订单',`<div class="form-grid"><label>来源渠道<select name="channel_id">${options(state.channels)}</select></label><label>履约门店<select name="store_id">${storeOptions()}</select></label><label>客户名称<input name="customer" required maxlength="80" placeholder="例如：零售顾客"></label><label>渠道订单号（选填）<input name="external_id" maxlength="80" placeholder="外部平台原始订单号"></label></div><div class="line-header"><span>商品</span><span>数量</span><span>成交单价 ¥</span><span></span></div><div id="order-lines"></div><button type="button" class="text-button" id="add-line">＋ 添加商品</button><div class="order-estimate">订单合计 <b id="estimated-total">¥0.00</b></div><p class="detail-note">确认后占用所选门店库存。收款与发货在订单详情中分别登记。</p>`,'创建并占用库存','/api/orders/',data=>({channel_id:data.get('channel_id'),store_id:data.get('store_id'),customer:data.get('customer'),external_id:data.get('external_id'),items:[...document.querySelectorAll('.order-form-line')].map(row=>({product_id:$('select',row).value,quantity:$('[data-quantity]',row).value,unit_price:$('[data-price]',row).value}))}));
    const lineHost=$('#order-lines');
    function updateTotal(){let cents=0;lineHost.querySelectorAll('.order-form-line').forEach(row=>{const value=$('[data-price]',row).value;const match=/^(\d+)(?:\.(\d{0,2}))?$/.exec(value);const price=match?Number(match[1])*100+Number((match[2]||'').padEnd(2,'0')):0;cents+=price*(+$('[data-quantity]',row).value||0);});$('#estimated-total').textContent='¥'+currency(cents/100);}
    function addLine(){const row=document.createElement('div');row.className='order-form-line';row.innerHTML=`<select aria-label="订单商品">${state.products.map(p=>`<option value="${p.id}">${esc(p.name)} · ${esc(p.sku)}</option>`).join('')}</select><input type="number" data-quantity min="1" max="1000000" step="1" value="1" required aria-label="商品数量"><input type="number" data-price min="0" max="9999999999.99" step="0.01" value="${state.products[0].price}" required aria-label="成交单价"><button type="button" class="line-remove" aria-label="移除商品行">×</button>`;lineHost.append(row);$('select',row).addEventListener('change',()=>{$('[data-price]',row).value=state.products.find(p=>p.id==$('select',row).value).price;updateTotal();});row.addEventListener('input',updateTotal);$('.line-remove',row).addEventListener('click',()=>{if(lineHost.children.length>1){row.remove();updateTotal();}else toast('订单至少保留一行商品。');});updateTotal();}
    $('#add-line').addEventListener('click',()=>{if(lineHost.children.length<100)addLine();});addLine();
  }
  function receiveStock(productId,storeId) {
    if(!state.products.length||!state.stores.length){toast('请先创建商品和门店。');return;}
    form('补货入库',`<p class="form-note">登记实际到货数量，增加门店实物库存。请在备注中保留来源单号。</p><div class="form-grid"><label>入库商品<select name="product_id">${options(state.products,'name',productId)}</select></label><label>入库门店<select name="store_id">${storeOptions(storeId)}</select></label><label>到货数量<input type="number" name="quantity" min="1" max="1000000" step="1" value="10" required></label><label class="span-all">来源单号 / 入库备注<input name="note" maxlength="240" required placeholder="例如：补货单 BH-20261104-01"></label></div>`,'确认入库','/api/stock/receive/',data=>Object.fromEntries(data));
  }
  function productForm(productId) {const p=state.products.find(item=>item.id==productId);form(p?'编辑商品':'新建商品',`<div class="form-grid"><label>商品 SKU<input name="sku" required maxlength="48" placeholder="例如 HX-001" value="${esc(p?.sku||'')}" ${p?'readonly':''}></label><label>商品名称<input name="name" required maxlength="120" value="${esc(p?.name||'')}"></label><label>商品分类<input name="category" required maxlength="48" value="${esc(p?.category||'生活用品')}"></label><label>参考售价 ¥<input name="price" type="number" step="0.01" min="0" max="9999999999.99" required value="${esc(p?.price||'')}"></label><label>门店库存预警阈值<input name="reorder_level" type="number" min="0" max="1000000" step="1" value="${p?.reorder_level??10}" required></label></div><p class="detail-note">${p?'SKU 固定；修改名称和售价不会影响历史订单快照。':'新商品初始库存为零，请到库存管理中补货入库。'}</p>`,p?'保存商品':'创建商品',p?`/api/products/${p.id}/update/`:'/api/products/',data=>Object.fromEntries(data));}
  function storeForm() {form('新建门店',`<div class="form-grid"><label>门店编码<input name="code" maxlength="32" required placeholder="例如 SH-03"></label><label>门店名称<input name="name" maxlength="80" required></label><label class="span-all">地址<input name="location" maxlength="160"></label></div>`,'创建门店','/api/stores/',data=>Object.fromEntries(data));}
  function channelForm() {form('新建渠道',`<div class="form-grid"><label>渠道编码<input name="code" maxlength="32" required placeholder="例如 DOUYIN"></label><label>渠道名称<input name="name" maxlength="80" required placeholder="例如 抖音小店"></label><label>渠道类型<select name="kind"><option value="online">线上渠道</option><option value="retail">线下门店</option></select></label></div><p class="detail-note">此操作建立渠道档案，平台自动拉单将在后续版本接入。</p>`,'创建渠道','/api/channels/',data=>Object.fromEntries(data));}
  function importForm() {let file=null;form('导入渠道订单',`<p class="form-note">按渠道编码和渠道订单号合并商品行，所有订单校验通过后统一创建。重复渠道订单号会拒绝整批导入。</p><a class="text-button" href="/api/export/template/">↓ 下载 CSV 列名模板</a><div class="upload-area"><strong>选择 UTF-8 编码的 CSV 文件</strong><br><input type="file" accept=".csv,text/csv" id="csv-file" required aria-label="订单 CSV 文件"><p>最多 100 张订单、500 行商品；门店、渠道与 SKU 需先创建。<br>导入后占用库存，收款与发货仍需单独确认。</p></div><p class="detail-note">渠道编码可在设计文档或演示导入文件中查询；演示：TMALL / DOUYIN / POS。</p>`,'校验并导入','/api/orders/import/',async()=>{if(!file)throw new Error('请选择 CSV 文件');if(file.size>500000)throw new Error('文件不能超过 500 KB');return {csv:await file.text()};});$('#csv-file').addEventListener('change',event=>{file=event.target.files[0];});}
  function detail(orderId) {
    const o=state.orders.find(order=>order.id==orderId);if(!o)return;
    const can=state.user.can_operate;
    let actions='';
    if(can&&o.status==='pending'&&!o.paid) actions=`<button class="button danger" data-order-action="cancel" data-id="${o.id}">取消订单</button><button class="button primary" data-order-action="pay" data-id="${o.id}">登记收款</button>`;
    if(can&&o.status==='pending'&&o.paid) actions=`<button class="button danger" data-order-action="refund" data-id="${o.id}">退款并取消</button><button class="button primary" data-order-action="ship" data-id="${o.id}">确认发货</button>`;
    if(can&&['shipped','partial_return'].includes(o.status)&&o.paid) actions=`<button class="button" data-order-action="refund" data-id="${o.id}">登记退货退款</button>`;
    showDialog('订单详情',`<div class="dialog-content"><div class="detail-summary"><div><small>订单编号</small><strong>${esc(o.number)}</strong></div><div><small>订单状态</small>${status(o)}</div><div><small>客户</small><strong>${esc(o.customer)}</strong></div><div><small>渠道 / 门店</small><strong>${esc(o.channel)} / ${esc(o.store)}</strong></div><div><small>渠道订单号</small><strong>${esc(o.external_id||'—')}</strong></div><div><small>收款状态</small><strong>${o.paid?'已登记收款':'未收款'}</strong></div></div>${table(['商品','数量 / 已退','成交单价','小计'],o.items.map(i=>`<tr><td>${esc(i.name)}<small>${esc(i.sku)}</small></td><td>${i.quantity} / ${i.returned_quantity}</td><td>¥${currency(i.unit_price)}</td><td>¥${currency(+i.unit_price*i.quantity)}</td></tr>`))}<div class="detail-total">订单金额 <b>¥${currency(o.total)}</b></div><div class="detail-note">已退款 ¥${currency(o.refunded_total)} · ${time(o.created_at)} 创建${o.payments.length?'<br>'+o.payments.map(p=>(p.kind==='receipt'?'收款':'退款')+' ¥'+currency(p.amount)+' · '+esc(p.reference)).join('<br>'):''}</div><div class="form-actions"><button class="button" data-close>关闭</button>${actions}</div></div>`);
  }
  function orderAction(id,action) {
    const o=state.orders.find(order=>order.id==id);if(!o)return;closeDialog();
    const path=`/api/orders/${o.id}/${action}/`;
    if(action==='pay') form('登记订单收款',`<p class="form-note">请确认已经实际收到 <strong>¥${currency(o.total)}</strong>，此操作只登记账务，不会向客户扣款。</p><div class="form-grid"><label class="span-all">收款凭证 / 交易流水号<input name="reference" maxlength="120" required placeholder="填写实际收款的交易凭证"></label></div>`,'确认已收款',path,data=>Object.fromEntries(data));
    if(action==='ship') form('确认订单发货',`<p class="form-note">${esc(o.number)} · ${esc(o.store)}<br>确认商品已交付或发出后，系统将扣减实物库存并释放占用。</p>`,'确认发货并扣库',path,()=>({}));
    if(action==='cancel') form('取消订单',`<p class="form-note">确认取消 ${esc(o.number)}？<br>所有商品占用将释放，实物库存不发生变化。</p>`,'取消并释放库存',path,()=>({}));
    if(action==='refund') {
      const pending=o.status==='pending';
      form(pending?'退款并取消订单':'登记退货退款',`<p class="form-note">请在实际退款后登记凭证。${pending?'本次全额退款 ¥'+currency(o.total)+'，并释放订单库存占用。':'按原成交单价退款；确认商品已收回且可再次销售时勾选回库。'}</p>${pending?'':o.items.filter(i=>i.quantity>i.returned_quantity).map(i=>`<div class="refund-row" data-line="${i.id}"><div>${esc(i.name)}<small>可退 ${i.quantity-i.returned_quantity} 件 · ¥${currency(i.unit_price)} / 件</small></div><input class="field-input" type="number" min="0" max="${i.quantity-i.returned_quantity}" step="1" value="0" aria-label="${esc(i.name)}退货数量"><label><input type="checkbox" checked>回库</label></div>`).join('')}<div class="form-grid" style="margin-top:20px"><label class="span-all">退款凭证 / 交易流水号<input name="reference" maxlength="120" required placeholder="填写实际退款的交易凭证"></label></div>`,'确认已退款',path,data=>({reference:data.get('reference'),...(pending?{}:{items:[...document.querySelectorAll('.refund-row')].filter(row=>+$('input[type=number]',row).value>0).map(row=>({line_id:row.dataset.line,quantity:$('input[type=number]',row).value,restock:$('input[type=checkbox]',row).checked}))})}));
    }
  }
  document.addEventListener('click',event=>{
    const target=event.target.closest('button,a');if(!target)return;
    if(target.dataset.page)navigate(target.dataset.page);
    if(target.dataset.nav)navigate(target.dataset.nav);
    if(target.dataset.status){filters.status=target.dataset.status;render();}
    if(target.hasAttribute('data-close'))closeDialog();
    if(target.dataset.order)detail(target.dataset.order);
    if(target.dataset.orderAction)orderAction(target.dataset.id,target.dataset.orderAction);
    const actions={order:newOrder,product:()=>productForm(target.dataset.product),store:storeForm,channel:channelForm,import:importForm,receive:()=>receiveStock(target.dataset.product,target.dataset.store)};
    if(target.dataset.action&&actions[target.dataset.action])actions[target.dataset.action]();
  });
  content.addEventListener('input',event=>{
    if(event.target.id!=='table-search')return;
    const position=event.target.selectionStart;filters.q=event.target.value;render();const input=$('#table-search');input.focus();input.setSelectionRange(position,position);
  });
  $('#store-filter').addEventListener('change',()=>load().catch(error=>toast(error.message)));
  $('#dialog-close').addEventListener('click',closeDialog);
  $('#menu-toggle').addEventListener('click',()=>$('.sidebar').classList.toggle('open'));
  dialog.addEventListener('click',event=>{if(event.target===dialog){const rect=dialog.getBoundingClientRect();if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom)closeDialog();}});
  if(labels[location.hash.slice(1)])page=location.hash.slice(1);
  let resizeTimer;
  window.addEventListener('resize',()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(()=>{if(state&&page==='dashboard'&&!dialog.open)render();},120);});
  load().catch(error=>{content.innerHTML=`<div class="empty">${esc(error.message)}<br><button class="text-button" id="retry-load">重试加载</button></div>`;$('#retry-load').addEventListener('click',()=>location.reload());});
})();
