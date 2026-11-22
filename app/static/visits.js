const visitUi={busy:false};const feedback=document.querySelector('#feedback');document.querySelector('#source').value=new URL(location.href).searchParams.get('dataset')||document.querySelector('#source').value;document.querySelector('#run').addEventListener('click',async()=>{if(visitUi.busy)return;visitUi.busy=true;feedback.textContent='核验中…';try{const dataset=document.querySelector('#source').value;const r=await fetch(dataset===document.body.dataset.source?'/api/runs/'+document.body.dataset.runId+'/rerun':'/api/runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({dataset})});const data=await r.json();if(!r.ok)throw Error(data.detail);const url=new URL(location.href);url.searchParams.set('dataset',dataset);url.searchParams.set('run_id',data.manifest.run_id);location.href=url;}catch(e){feedback.textContent=e.message;}finally{visitUi.busy=false;}});
const filePicker=document.querySelector('#csv-file');
if(filePicker){
 const editor=document.querySelector('#csv-text');
 filePicker.addEventListener('change',async()=>{const file=filePicker.files[0];if(!file)return;if(file.size>2000000){editor.value='';alert('文件超过2MB，请缩小后导入');return;}const bytes=await file.arrayBuffer();try{editor.value=new TextDecoder('utf-8',{fatal:true}).decode(bytes);}catch(e){editor.value=new TextDecoder('gb18030',{fatal:true}).decode(bytes);}});
 document.querySelector('#import-csv').addEventListener('click',async event=>{event.target.disabled=true;try{const response=await fetch('/api/runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csv_text:editor.value})});const data=await response.json();if(!response.ok)throw Error(data.detail);location.href=location.pathname+'?run_id='+data.manifest.run_id;}catch(e){editor.setCustomValidity(e.message);editor.reportValidity();}finally{event.target.disabled=false;}});
 editor.addEventListener('input',()=>editor.setCustomValidity(''));
}
// 澄清回复：同内容重试由后端幂等处理，前端只防连点。
const replyForms=document.querySelectorAll('.reply-form');
replyForms.forEach(form=>{
 form.addEventListener('submit',async event=>{
  event.preventDefault();
  if(form.dataset.busy==='1')return;
  const author=form.querySelector('[name=author]'),text=form.querySelector('[name=text]'),close=form.querySelector('[name=close]');
  if(!text.value.trim()){text.setAttribute('aria-invalid','true');text.focus();return;}
  form.dataset.busy='1';const button=form.querySelector('button');button.disabled=true;feedback.textContent='提交澄清回复…';
  try{
   const response=await fetch('/api/clarify/reply',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({run_id:document.body.dataset.runId,query_id:form.dataset.query,author:author.value,text:text.value,close:close&&close.checked})});
   const data=await response.json();
   if(!response.ok)throw Error(data.detail);
   const url=new URL(location.href);url.searchParams.set('run_id',data.run.manifest.run_id);location.href=url;
  }catch(e){feedback.textContent=e.message;form.dataset.busy='';button.disabled=false;}
 });
});
// 澄清页：载入示例未关闭问题清单，便于对照裁决口径。
const sampleButton=document.querySelector('#load-sample');
if(sampleButton){
 sampleButton.addEventListener('click',async()=>{
  sampleButton.disabled=true;feedback.textContent='载入示例澄清清单…';
  try{
   const queries=await (await fetch('/api/clarify/sample')).json();
   const response=await fetch('/api/runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({dataset:document.body.dataset.source,queries})});
   const data=await response.json();
   if(!response.ok)throw Error(data.detail);
   const url=new URL(location.href);url.searchParams.set('run_id',data.manifest.run_id);location.href=url;
  }catch(e){feedback.textContent=e.message;sampleButton.disabled=false;}
 });
}
// 复核批次：提交当前 revision，陈旧提交由后端 409。
const reviewForm=document.querySelector('#review-form');
if(reviewForm){
 reviewForm.addEventListener('submit',async event=>{
  event.preventDefault();if(reviewForm.dataset.busy==='1')return;reviewForm.dataset.busy='1';
  const button=reviewForm.querySelector('button');button.disabled=true;feedback.textContent='提交复核…';
  try{
   const current=await (await fetch('/api/runs/'+document.body.dataset.runId)).json();
   const response=await fetch('/api/runs/'+document.body.dataset.runId+'/review',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({revision:current.revision})});
   const data=await response.json();
   if(!response.ok)throw Error(data.detail);
   location.reload();
  }catch(e){feedback.textContent=e.message;reviewForm.dataset.busy='';button.disabled=false;}
 });
}
