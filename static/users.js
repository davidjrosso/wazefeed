'use strict';
function ViaUsers({$,el,button,api,getState,message,format}) {
  let data, mode, target, revision, saving=false;
  const roles={admin:'Administrador',operator:'Operador',viewer:'Consulta'};
  const dialog=$('user-dialog');
  const nav=button('Usuarios',()=>{},'nav-tab'); nav.id='users-nav'; nav.dataset.view='users'; nav.hidden=true;
  $('legacy').before(nav);
  const clearSecrets=()=>{for(const id of ['user-password','user-repeat','user-admin-password']) $(id).value='';};
  function impact() {
    const text=mode==='password'?'Se cerrarán sus sesiones abiertas. Deberá ingresar con la nueva contraseña.' :
      mode==='access'&&$('user-enabled').value==='no'?'La cuenta no podrá ingresar. Sus cierres y agendas se conservan; no se reabren calles.' :
      $('user-role').value==='admin'?'Control completo: podrá publicar cierres, editar plantillas y gestionar otras cuentas.' :
      $('user-role').value==='viewer'?'Solo lectura. No podrá publicar, reabrir, cambiar agendas ni gestionar usuarios.' :
      'Podrá publicar y levantar cierres reales en el feed. No es un modo de práctica.';
    $('user-impact').textContent=text+(mode==='access'?' Los cambios cierran sus sesiones actuales.':'');
  }
  function edit(action,row) {
    if(!data || getState().role!=='admin') return;
    mode=action;target=row;revision=data.revision;clearSecrets();
    $('user-form').reset();$('user-error').textContent='';
    $('user-title').textContent=action==='create'?'Sumar una persona':action==='access'?'Permisos de '+row.username:'Contraseña de '+row.username;
    $('user-name').value=row?.username||'';$('user-name').readOnly=action!=='create';
    $('user-role').value=row?.role||'operator';$('user-enabled').value=row?.enabled===false?'no':'yes';
    $('user-role-field').hidden=action==='password';$('user-enabled-field').hidden=action!=='access';
    $('user-password-fields').hidden=action==='access';
    for(const id of ['user-password','user-repeat']) $(id).required=action!=='access';
    $('user-save').textContent=action==='create'?'Crear cuenta':action==='access'?'Guardar permisos':'Cambiar contraseña';
    impact();dialog.showModal();
  }
  function render() {
    const list=$('users-list');list.replaceChildren();
    for(const row of data.accounts) {
      const card=el('article','account-card'+(!row.enabled?' account-disabled':'')), head=el('div','account-head');
      head.append(el('span','account-avatar',row.username.slice(0,2).toUpperCase()),el('h3','',row.username));card.append(head);
      card.append(el('p','',roles[row.role]+' · '+(row.enabled?'Activo':'Desactivado')));
      if(row.protected) card.append(el('small','field-help','Cuenta de recuperación protegida. Su contraseña se administra desde el servidor.'));
      else {
        const actions=el('div','run-actions');actions.append(button('Permisos y estado',()=>edit('access',row)),button('Cambiar contraseña',()=>edit('password',row)));card.append(actions);
      }
      list.append(card);
    }
    const audit=$('users-audit');audit.replaceChildren();
    const names={create:'Creó',access:'Cambió permisos de',password:'Cambió contraseña de'};
    for(const row of data.audit) audit.append(el('p','field-help',format(row.at)+' · '+row.actor+' · '+(names[row.action]||row.action)+' '+row.subject+' · '+roles[row.role]+' · '+(row.enabled?'activo':'desactivado')));
    if(!data.audit.length)audit.append(el('p','field-help','Los próximos cambios de acceso quedarán registrados aquí.'));
  }
  async function load() {
    if(getState().role!=='admin')return;
    try {data=await api('/api/users');render();}
    catch(e){message('No se pudo cargar Usuarios: '+e.message,true);}
  }
  $('new-user').onclick=()=>edit('create');$('refresh-users').onclick=load;
  $('user-role').onchange=impact;$('user-enabled').onchange=impact;
  dialog.addEventListener('close',clearSecrets);
  dialog.addEventListener('cancel',e=>{if(saving)e.preventDefault();});
  $('user-form').onsubmit=async e=>{
    e.preventDefault();if(saving)return;
    $('user-error').textContent='';
    if(mode!=='access'&&$('user-password').value!==$('user-repeat').value){$('user-error').textContent='Las contraseñas nuevas no coinciden.';return;}
    const body={action:mode,revision,username:$('user-name').value,role:$('user-role').value,enabled:$('user-enabled').value==='yes',adminPassword:$('user-admin-password').value};
    if(mode!=='access')body.password=$('user-password').value;
    saving=true;dialog.querySelectorAll('button,input,select').forEach(b=>b.disabled=true);
    try {
      data=await api('/api/users',body);render();dialog.close();
      if(target?.username===getState().user){location.href=document.body.dataset.base+'/login';return;}
      message('Cuenta actualizada. No se modificaron cierres ni agendas.');
    } catch(error) {
      $('user-error').textContent=error.message+' Actualizá la lista para comprobar el resultado antes de volver a intentarlo.';
    } finally {
      delete body.password;delete body.adminPassword;clearSecrets();saving=false;
      dialog.querySelectorAll('button,input,select').forEach(b=>b.disabled=false);
    }
  };
  return {load};
}
