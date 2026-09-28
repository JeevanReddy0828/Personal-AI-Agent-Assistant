/* The original window alone completes sign-in; Google tokens never enter page storage. */
window.jarvisGoogle = async function({purpose='signin', current='', message, cancelButton}) {
  const native = new URLSearchParams(location.search).get('app') === '1';
  let popup = native ? null : window.open('about:blank', '_blank');
  if(popup)popup.opener=null;
  let flow='', cancelled=false;
  const post=async(path,body)=>{
    const r=await fetch('/auth/google/'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const d=await r.json();
    if(!r.ok)throw Error(d.message||'Could not complete Google sign-in.');
    return d;
  };
  const cancel=()=>{cancelled=true;if(flow)post('cancel',{flow}).catch(()=>{});};
  cancelButton.hidden=false;cancelButton.onclick=cancel;
  window.addEventListener('pagehide',cancel);
  try{
    if(!native&&!popup)throw Error('Allow a pop-up for this app and try again.');
    message.textContent='Opening Google in your browser…';
    const d=await post('start',{purpose,current,native});flow=d.flow;
    if(cancelled){await post('cancel',{flow});return;}
    if(d.launch){
      if(!popup)popup=window.open(d.launch,'_blank');else popup.location=d.launch;
      if(!popup)throw Error('Allow a pop-up for this app and try again.');
    }
    message.textContent='Finish with Google in your browser, then return here. This attempt expires in ten minutes.';
    const end=Date.now()+600000;
    while(!cancelled&&Date.now()<end){
      const done=await post('complete',{flow});
      if(!done.pending){location.reload();return;}
      await new Promise(resolve=>setTimeout(resolve,1000));
    }
    message.textContent=cancelled?'Google sign-in cancelled.':'Google sign-in expired. Try again.';
  }catch(e){message.textContent=e.message||'Could not reach the app.';}
  finally{
    if(flow)post('cancel',{flow}).catch(()=>{});
    if(popup&&!popup.closed)popup.close();
    cancelButton.hidden=true;window.removeEventListener('pagehide',cancel);
  }
};
