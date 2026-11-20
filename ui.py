PAGE = r'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Zepp Step Manager</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f5f6f8;color:#202124;font:16px/1.6 system-ui,"Microsoft YaHei",sans-serif}
header{padding:18px max(20px,calc((100vw - 1080px)/2));background:#fff;border-bottom:1px solid #e5e7eb;font-weight:700}
main{max-width:1080px;margin:38px auto;padding:0 20px}h1{font-size:32px;margin:0 0 8px}h2{font-size:20px;margin:0 0 16px}p,.hint{color:#666}p{margin:0 0 22px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}.card{background:#fff;border:1px solid #e5e7eb;border-radius:16px;padding:22px}
label{display:block;font-size:14px;font-weight:600;margin:14px 0 6px}input,select{width:100%;padding:11px;border:1px solid #cbd0d8;border-radius:9px;font:inherit;background:white}
button{border:0;border-radius:9px;padding:11px 16px;background:#1769d2;color:white;font:inherit;cursor:pointer;margin-top:12px}
button:disabled{opacity:.55}.secondary{background:#eaf1fb;color:#1758a6}.full{width:100%}.row{display:flex;gap:10px;align-items:center}.row>*{flex:1;min-width:0}
.tabs{display:flex;gap:8px}.tabs button{flex:1}.tabs button[aria-selected=true]{background:#174f9c}
.status{padding:12px;background:#f1f3f5;border-radius:9px;margin:14px 0;overflow-wrap:anywhere}.good{background:#e9f7ed;color:#176b32}.bad{background:#fff0ef;color:#a22}
.captcha{max-width:140px;height:54px;object-fit:contain}.hint{font-size:12px;margin-top:8px}#qr{text-align:center;margin-top:12px}#qr img{max-width:230px;width:100%}
.banner{background:#fff;border:1px solid #ddd;border-radius:9px;padding:12px;margin:18px 0}footer{font-size:12px;color:#777;margin:20px 0}
[hidden]{display:none!important}@media(max-width:720px){.grid{grid-template-columns:1fr}main{margin-top:24px}}
</style></head>
<body><header>Zepp Step Manager</header><main>
<section id="unlock" class="card" {% if not locked %}hidden{% endif %}>
<h2>解锁</h2><form id="unlockForm"><label>管理密码</label><input id="admin" type="password" required><button class="full">解锁</button></form>
</section>
<section id="workspace" {% if locked %}hidden{% endif %}>
<h1>小米运动步数助手</h1><p>注册或登录 Zepp Life 账号，选择已绑定设备，提交步数，也可以绑定微信和支付宝。</p>
<div class="grid">
<section class="card">
<div class="tabs"><button type="button" id="registerTab" aria-selected="true">注册新账号</button><button type="button" id="loginTab" aria-selected="false" class="secondary">已有账号登录</button></div>
<form id="accountForm">
<label for="email">Zepp 邮箱</label><input id="email" type="email" autocomplete="username" required maxlength="254" placeholder="name@example.com">
<label for="password">密码</label><input id="password" type="password" minlength="8" maxlength="128" required autocomplete="new-password">
<div id="captchaArea"><label for="code">图片验证码</label><div class="row"><input id="code" maxlength="16" placeholder="输入验证码"><img id="captchaImage" class="captcha" alt="验证码图片" hidden></div><button type="button" id="refreshCaptcha" class="secondary">获取验证码</button></div>
<button id="submitAccount" class="full">注册并登录</button><div class="hint">登录信息加密保存在本机，不会上传到本项目的其他服务器。</div>
</form>
</section>
<section class="card">
<h2>当前账号</h2><label for="accounts">已保存的账号</label><select id="accounts"><option value="">请先注册或登录</option></select>
<div class="hint">切换账号后，下面的设备和绑定状态会自动刷新。</div>
</section>
<section class="card">
<h2>设备与步数</h2><label for="devices">已绑定设备</label><select id="devices"><option value="">请先选择账号</option></select>
<button id="loadDevices" class="secondary">刷新设备</button>
<label for="steps">要提交的步数</label><input id="steps" type="number" min="0" max="200000" step="1" placeholder="例如 10000">
<button id="submitSteps" class="full">提交步数</button><div id="stepsStatus" class="status">选择账号和设备后提交。</div>
</section>
<section class="card">
<h2>微信运动</h2><div id="wechatStatus" class="status">选择账号后查询绑定状态。</div>
<div class="row"><button id="checkWechat" class="secondary">刷新状态</button><button id="getWechatQr">获取绑定二维码</button></div>
<div id="qr" hidden><img id="qrImage" alt="微信绑定二维码"><div class="hint">使用微信扫一扫，完成后刷新状态确认。</div></div>
</section>
<section class="card">
<h2>支付宝</h2><div id="alipayStatus" class="status">选择账号后查询绑定状态。</div>
<div class="row"><button id="checkAlipay" class="secondary">刷新状态</button><button id="getAlipay">获取授权入口</button></div>
<div id="alipayLink" class="hint"></div>
</section>
</div>
<footer>接口可能随 Zepp Life 版本变化。请勿公开分享账号、密码、数据库或本机密钥。</footer>
</section>
<div id="notice" class="banner" role="status" hidden></div>
</main>
<script>
const csrf={{ csrf|tojson }}, locked={{ locked|tojson }};
const $=id=>document.getElementById(id);
let mode="register",challenge="",busy=false;
function notice(text){$("notice").textContent=text;$("notice").hidden=!text;}
async function api(path,body){const r=await fetch(path,{method:body===undefined?"GET":"POST",headers:{"Content-Type":"application/json","X-CSRF-Token":csrf},body:body===undefined?undefined:JSON.stringify(body)});let d;try{d=await r.json()}catch{throw Error("服务响应异常。")}if(!r.ok)throw Error(d.error||"请求失败");return d;}
function accountId(){return $("accounts").value;}
function setMode(v){mode=v;$("registerTab").setAttribute("aria-selected",v==="register");$("loginTab").setAttribute("aria-selected",v==="login");$("captchaArea").hidden=v==="login";$("submitAccount").textContent=v==="register"?"注册并登录":"登录并保存账号";$("password").autocomplete=v==="register"?"new-password":"current-password";notice("");}
$("registerTab").onclick=()=>setMode("register");$("loginTab").onclick=()=>setMode("login");
async function loadAccounts(selected){const d=await api("/api/accounts");$("accounts").replaceChildren();if(!d.accounts.length)$("accounts").append(new Option("暂无账号",""));else{d.accounts.forEach(a=>$("accounts").append(new Option(a.account,a.id)));if(selected)$("accounts").value=selected;}}
async function refreshCaptcha(){const b=$("refreshCaptcha");b.disabled=true;challenge="";$("captchaImage").hidden=true;$("code").value="";try{const d=await api("/api/captcha",{});challenge=d.challenge_id;$("captchaImage").src=d.image;$("captchaImage").hidden=false;}catch(e){notice(e.message)}finally{b.disabled=false;}}
async function loadDevices(){const id=accountId();if(!id)throw Error("请先注册或登录。");const d=await api("/api/accounts/"+id+"/devices");$("devices").replaceChildren();d.devices.forEach(x=>$("devices").append(new Option(x.name+" · "+x.device_id,x.device_id)));$("stepsStatus").textContent="找到 "+d.devices.length+" 台设备，请选择设备。";}
async function checkWechat(qr){const id=accountId();if(!id)throw Error("请先选择账号。");const d=await api("/api/accounts/"+id+"/wechat",{qr:!!qr});$("wechatStatus").className=d.bound?"status good":"status";$("wechatStatus").textContent=d.bound?"已绑定微信"+(d.nickname?" · "+d.nickname:""):"尚未绑定微信";if(d.qr_image){$("qrImage").src=d.qr_image;$("qr").hidden=false;}else{$("qr").hidden=true;}}
async function checkAlipay(){const id=accountId();if(!id)throw Error("请先选择账号。");const d=await api("/api/accounts/"+id+"/alipay",{action:"status"});$("alipayStatus").className=d.bound?"status good":"status";$("alipayStatus").textContent=d.bound?"支付宝已绑定":"支付宝尚未绑定";}
async function getAlipay(){const id=accountId();if(!id)throw Error("请先选择账号。");const d=await api("/api/accounts/"+id+"/alipay",{action:"authorize"});$("alipayLink").replaceChildren();const a=document.createElement("a");a.href=d.scheme||d.url;a.target="_blank";a.rel="noopener noreferrer";a.textContent="打开支付宝授权";$("alipayLink").append(a);}
async function submitSteps(){const id=accountId(),device_id=$("devices").value,steps=Number($("steps").value);if(!id||!device_id)throw Error("请先选择账号和设备。");if(!Number.isInteger(steps)||steps<0||steps>200000)throw Error("步数请输入 0 到 200000 之间的整数。");$("submitSteps").disabled=true;$("stepsStatus").textContent="正在提交…";try{const d=await api("/api/accounts/"+id+"/steps",{device_id,steps});$("stepsStatus").className="status good";$("stepsStatus").textContent="提交成功："+d.steps+" 步";}catch(e){$("stepsStatus").className="status bad";$("stepsStatus").textContent="提交失败："+e.message;}finally{$("submitSteps").disabled=false;}}
$("refreshCaptcha").onclick=refreshCaptcha;
$("accountForm").onsubmit=async e=>{e.preventDefault();if(busy)return;if(mode==="register"&&(!challenge||!$("code").value.trim())){notice("请先获取并填写图片验证码。");return;}busy=true;$("submitAccount").disabled=true;try{const d=await api("/api/account",{mode,account:$("email").value,password:$("password").value,code:$("code").value,challenge_id:challenge});$("password").value="";await loadAccounts(d.id);notice(d.warning||(d.created?"注册成功。":"登录成功。"));if(!d.warning){await loadDevices().catch(e=>$("stepsStatus").textContent=e.message);await checkWechat(false).catch(e=>$("wechatStatus").textContent=e.message);await checkAlipay().catch(e=>$("alipayStatus").textContent=e.message);}}catch(e){notice(e.message)}finally{challenge="";$("captchaImage").hidden=true;busy=false;$("submitAccount").disabled=false;setMode(mode);}};
$("accounts").onchange=()=>{const run=async()=>{await loadDevices().catch(e=>$("stepsStatus").textContent=e.message);await checkWechat(false).catch(e=>$("wechatStatus").textContent=e.message);await checkAlipay().catch(e=>$("alipayStatus").textContent=e.message);};run();};
$("loadDevices").onclick=()=>loadDevices().catch(e=>notice(e.message));
$("submitSteps").onclick=()=>submitSteps().catch(e=>notice(e.message));
$("checkWechat").onclick=()=>checkWechat(false).catch(e=>notice(e.message));
$("getWechatQr").onclick=()=>checkWechat(true).catch(e=>notice(e.message));
$("checkAlipay").onclick=()=>checkAlipay().catch(e=>notice(e.message));
$("getAlipay").onclick=()=>getAlipay().catch(e=>notice(e.message));
$("unlockForm").onsubmit=async e=>{e.preventDefault();try{await api("/api/unlock",{password:$("admin").value});location.reload();}catch(e){notice(e.message);}};
if(!locked)loadAccounts().catch(e=>notice(e.message));
</script></body></html>'''
