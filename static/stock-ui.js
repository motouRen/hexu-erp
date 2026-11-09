window.HexuStock = function(ctx) {
  const {esc,time,table,showDialog,closeDialog,options,searchbox,exportButton,api,toast} = ctx;
  const $ = (selector, root=document) => root.querySelector(selector);
  const state = ctx.getState;
  function form(...args){const build=args[4];args[4]=(data,element)=>{const result=build(data,element);if(result.kind==='count'){document.querySelectorAll('.stock-form-line').forEach((row,index)=>{if(row.dataset.version===undefined)throw new Error('库存快照正在读取，请稍后保存。');result.items[index].expected_version=row.dataset.version;});}return result;};return ctx.form(...args);}
  function render() {
    const tabs=[['all','全部单据'],['draft','草稿'],['submitted','待审批'],['posted','已过账'],['canceled','已撤销']];
    return `<section class="panel"><div class="filter-tabs">${tabs.map(([value,label])=>`<button class="filter-tab ${ctx.getFilters().status===value?'active':''}" data-status="${value}">${label}</button>`).join('')}</div><div class="toolbar">${searchbox('搜索库存单号、来源或商品')}${exportButton('documents')}</div>${table(['单据编号 / 来源','业务类型','门店 / 调入门店','状态','创建人 / 审批人','创建时间',''],(state().documents||[]).map(d=>`<tr><td><button class="text-button order-number" data-document="${d.id}">${esc(d.number)}</button><small>${esc(d.source||'—')}</small></td><td>${esc(d.kind_label)}</td><td>${esc(d.store)}${d.target_store?'<small>→ '+esc(d.target_store)+'</small>':''}</td><td><span class="status ${d.status}">${esc(d.status_label)}</span></td><td>${esc(d.created_by)}<small>${esc(d.approved_by||'未审批')}</small></td><td>${time(d.created_at)}</td><td><button class="text-button" data-document="${d.id}">查看 →</button></td></tr>`))}</section>`;
  }
  function create() {
    const s=state();
    if(!s.stores.length||!s.products.length){toast('请先建立商品和门店。');return;}
    form('新建库存单据',`<div class="form-grid"><label>业务类型<select name="kind" id="stock-kind"><option value="receive">补货入库</option><option value="transfer">门店调拨</option><option value="count">库存盘点</option></select></label><label>业务门店 / 调出门店<select name="store_id" id="stock-store">${options(s.stores)}</select></label><label id="target-store-field" hidden>调入门店<select name="target_store_id">${options(s.stores,'name',s.stores[1]?.id)}</select></label><label>来源单号（选填）<input name="source" maxlength="120" placeholder="例如：供应商送货单号"></label><label class="span-all">业务说明<input name="note" maxlength="240" placeholder="例如：季节性补货或门店盘点"></label></div><div class="line-header stock-line-layout"><span>商品</span><span id="quantity-label">数量</span><span></span></div><div id="stock-lines"></div><button type="button" class="text-button" id="stock-add-line">＋ 添加商品</button><p class="form-note" id="stock-rule" style="margin-top:18px"></p>`, '保存草稿', '/api/stock/documents/', data=>({kind:data.get('kind'),store_id:data.get('store_id'),...(data.get('kind')==='transfer'?{target_store_id:data.get('target_store_id')}:{}),source:data.get('source'),note:data.get('note'),items:[...document.querySelectorAll('.stock-form-line')].map(row=>({product_id:$('select',row).value,quantity:$('input',row).value}))}));
    const host=$('#stock-lines');let stockRequest=0;
    host.addEventListener('input',event=>{const row=event.target.closest('.stock-form-line');if(row&&event.target.tagName==='INPUT')row.dataset.edited='1';});
    async function refreshRow(row) {
      const token=++stockRequest,product=s.products.find(p=>p.id==$('select',row).value), store=$('#stock-store').value;
      row.dataset.request=String(token);row.dataset.edited='';delete row.dataset.version;
      try{
        const result=await api(`/api/records/inventory/?store=${store}&q=${encodeURIComponent(product.sku)}&page_size=100`);
        if(!row.isConnected||store!==$('#stock-store').value||row.dataset.request!==String(token)||$('select',row).value!=product.id)return;
        const balance=result.results.find(i=>i.product_id===product.id);
        row.dataset.version=String(balance?.version||0);
        $('.stock-snapshot',row).textContent=`当前实物 ${balance?.on_hand||0} · 占用 ${balance?.reserved||0}`;
        if($('#stock-kind').value==='count'&&!row.dataset.edited)$('input',row).value=balance?.on_hand||0;
      }catch(error){if(row.isConnected)$('.stock-snapshot',row).textContent=error.message;}
    }
    function refreshRules(){const kind=$('#stock-kind').value;$('#target-store-field').hidden=kind!=='transfer';$('#quantity-label').textContent=kind==='count'?'实盘数量':'数量';$('#stock-rule').textContent={receive:'先保存草稿，到货核对后在详情中过账。',transfer:'调出与调入在同一事务内完成；需要两家门店的访问权限。',count:'请填写实际清点数量，提交后由另一名审批人确认；期间发生库存变化需重新盘点。'}[kind];host.querySelectorAll('.stock-form-line').forEach(row=>{$('input',row).min=kind==='count'?'0':'1';refreshRow(row);});}
    function addLine(){const row=document.createElement('div');row.className='stock-form-line';row.innerHTML=`<div><select aria-label="库存单商品">${options(s.products)}</select><small class="stock-snapshot">正在读取当前库存…</small></div><input type="number" aria-label="库存单数量" min="1" max="1000000" step="1" required value="1"><button class="line-remove" type="button" aria-label="移除商品">×</button>`;host.append(row);$('select',row).addEventListener('change',()=>refreshRow(row));$('.line-remove',row).addEventListener('click',()=>{if(host.children.length>1)row.remove();});refreshRow(row);}
    $('#stock-add-line').addEventListener('click',()=>{if(host.children.length<100){addLine();refreshRules();}});
    $('#stock-kind').addEventListener('change',refreshRules);$('#stock-store').addEventListener('change',refreshRules);addLine();refreshRules();
  }
  function detail(id) {
    const d=state().documents.find(item=>item.id==id);if(!d)return;
    const actor=state().user,can=actor.can_operate;
    let actions='';
    const button=(action,label,primary=false)=>`<button class="button ${primary?'primary':''}" data-stock-action="${action}" data-id="${d.id}">${label}</button>`;
    if(can&&d.status==='draft')actions+=button('cancel','撤销草稿')+button(d.kind==='count'?'submit':'post',d.kind==='count'?'提交审批':'确认过账',true);
    if(can&&d.status==='submitted'){actions+=button('cancel','撤销申请');if(actor.can_approve&&actor.id!==d.created_by_id)actions+=button('post','审批并调整库存',true);}
    if(can&&d.status==='posted'&&d.kind!=='count')actions+=button('cancel','申请撤销过账');
    showDialog('库存单据详情',`<div class="dialog-content"><div class="detail-summary"><div><small>单据编号</small><strong>${esc(d.number)}</strong></div><div><small>单据类型</small><strong>${esc(d.kind_label)}</strong></div><div><small>当前状态</small><span class="status ${d.status}">${esc(d.status_label)}</span></div><div><small>业务门店</small><strong>${esc(d.store)}</strong></div><div><small>调入门店</small><strong>${esc(d.target_store||'—')}</strong></div><div><small>创建人 / 审批人</small><strong>${esc(d.created_by)} / ${esc(d.approved_by||'—')}</strong></div></div>${table(d.kind==='count'?['商品','账面 / 占用','实盘数量','差异']:['商品','数量',''],d.items.map(i=>d.kind==='count'?`<tr><td>${esc(i.name)}<small>${esc(i.sku)}</small></td><td>${i.snapshot_quantity} / ${i.snapshot_reserved}</td><td>${i.quantity}</td><td class="delta ${i.difference<0?'negative':''}">${i.difference>0?'+':''}${i.difference}</td></tr>`:`<tr><td>${esc(i.name)}<small>${esc(i.sku)}</small></td><td>${i.quantity}</td><td>件</td></tr>`))}<p class="detail-note">来源：${esc(d.source||'—')}<br>说明：${esc(d.note||'—')}${d.approval_note?'<br>审批：'+esc(d.approval_note):''}<br>创建于 ${time(d.created_at)}</p>${d.status==='posted'&&d.kind!=='count'?'<p class="detail-note">仅当相关商品没有后续库存业务时允许撤销。原流水保留，并新增反向流水。</p>':''}<div class="form-actions"><button class="button" data-close>关闭</button>${actions}</div></div>`);
  }
  function action(id,action) {
    const d=state().documents.find(item=>item.id==id);if(!d)return;closeDialog();
    const description={post:d.kind==='count'?'确认实盘差异，审批通过后将调整实物库存。':'确认商品已实际到货或移交，过账后同步库存及流水。',submit:'提交盘点结果，等待另一名审批人确认。',cancel:d.status==='posted'?'撤销将生成反向流水；已有后续业务时会拒绝。':'撤销此草稿或审批申请，库存不会发生变化。'}[action];
    form({post:'确认库存过账',submit:'提交盘点审批',cancel:'撤销库存单据'}[action],`<p class="form-note">${esc(d.number)}<br>${description}</p>${d.kind==='count'&&action==='post'?'<label class="form-label">审批说明<input class="field-input" name="note" maxlength="240" placeholder="填写核对结果"></label>':''}`,'确认提交',`/api/stock/documents/${d.id}/${action}/`,data=>Object.fromEntries(data));
  }
  return {render,create,detail,action};
};
