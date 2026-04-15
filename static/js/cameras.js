(function(){
  "use strict";

  /* ================================================================
     Utilities
     ================================================================ */
  var $ = function(s, r){ return (r||document).querySelector(s); };
  var $$ = function(s, r){ return Array.from((r||document).querySelectorAll(s)); };

  var CSRF = (document.querySelector('meta[name="csrf-token"]') || {}).content || "";

  function jsonHeaders(){
    return {"Content-Type": "application/json", "X-CSRFToken": CSRF};
  }

  function escHtml(s){
    var d = document.createElement("div");
    d.textContent = s || "";
    return d.innerHTML;
  }

  function toast(msg, type){
    var box = $("#toast-box");
    if(!box) return;
    var el = document.createElement("div");
    el.className = "toast " + (type || "info");
    el.textContent = msg;
    box.appendChild(el);
    setTimeout(function(){ if(el.parentNode) el.parentNode.removeChild(el); }, 3200);
  }

  /* ================================================================
     API
     ================================================================ */
  var API = {
    list:       function(){ return fetch("/api/cameras").then(function(r){ return r.json(); }); },
    create:     function(d){ return fetch("/api/cameras", {method:"POST", headers:jsonHeaders(), body:JSON.stringify(d)}).then(function(r){ return r.json(); }); },
    update:     function(id,d){ return fetch("/api/cameras/"+id, {method:"PUT", headers:jsonHeaders(), body:JSON.stringify(d)}).then(function(r){ return r.json(); }); },
    remove:     function(id){ return fetch("/api/cameras/"+id, {method:"DELETE", headers:jsonHeaders()}).then(function(r){ return r.json(); }); },
    status:     function(id){ return fetch("/api/cameras/"+id+"/status").then(function(r){ return r.json(); }); },
    capture:    function(id){ return fetch("/api/cameras/"+id+"/capture", {method:"POST", headers:{"X-CSRFToken": CSRF}}); },
    action:     function(id,d){ return fetch("/api/cameras/"+id+"/action", {method:"POST", headers:jsonHeaders(), body:JSON.stringify(d)}).then(function(r){ return r.json(); }); },
    testConn:   function(id){ return fetch("/api/cameras/"+id+"/test", {method:"POST", headers:{"X-CSRFToken": CSRF}}).then(function(r){ return r.json(); }); },
    testNew:    function(d){ return fetch("/api/cameras/test-connection", {method:"POST", headers:jsonHeaders(), body:JSON.stringify(d)}).then(function(r){ return r.json(); }); }
  };

  /* ================================================================
     State
     ================================================================ */
  var _cameras = [];
  var _statusCache = {};
  var _currentDetail = null;
  var _statusTimer = null;

  /* ================================================================
     Grid rendering
     Note: all dynamic values are escaped via escHtml() before
     insertion. Camera data comes from our own authenticated API.
     ================================================================ */
  function renderGrid(cameras){
    _cameras = cameras || [];
    var grid = $("#camera-grid");
    var empty = $("#cam-empty");
    var countText = $("#cam-count-text");

    if(!_cameras.length){
      grid.textContent = "";
      empty.style.display = "block";
      if(countText) countText.textContent = "0 cameras";
      return;
    }
    empty.style.display = "none";
    if(countText) countText.textContent = _cameras.length + " camera" + (_cameras.length>1?"s":"");

    /* Build cards using DOM to avoid raw innerHTML with user data */
    grid.textContent = "";
    _cameras.forEach(function(cam){
      var st = _statusCache[cam._id];
      var statusClass = st ? (st.online ? "online" : "offline") : "unknown";
      var statusLabel = st ? (st.online ? "En ligne" : "Hors ligne") : "...";
      var snapUrl = "/api/cameras/"+encodeURIComponent(cam._id)+"/snapshot";

      var card = document.createElement("div");
      card.className = "cam-card" + (cam.enabled ? "" : " disabled");
      card.dataset.id = cam._id;

      // Preview
      var preview = document.createElement("div");
      preview.className = "cam-card-preview";

      var thumb = document.createElement("img");
      thumb.className = "cam-thumb";
      thumb.src = snapUrl;
      thumb.alt = "";
      thumb.loading = "lazy";
      thumb.style.display = "none";
      thumb.draggable = false;

      var placeholder = document.createElement("span");
      placeholder.className = "material-symbols-outlined cam-placeholder";
      placeholder.textContent = "videocam";

      var overlay = document.createElement("div");
      overlay.className = "cam-card-overlay";

      var badge = document.createElement("div");
      badge.className = "cam-card-badge";
      var dot = document.createElement("span");
      dot.className = "cam-status " + statusClass;
      badge.appendChild(dot);
      badge.appendChild(document.createTextNode(" " + statusLabel));

      preview.appendChild(thumb);
      preview.appendChild(placeholder);
      preview.appendChild(overlay);
      preview.appendChild(badge);

      // Info
      var info = document.createElement("div");
      info.className = "cam-card-info";
      var infoDot = document.createElement("span");
      infoDot.className = "cam-status " + statusClass;
      var nameSpan = document.createElement("span");
      nameSpan.className = "cam-card-name";
      nameSpan.textContent = cam.name;
      info.appendChild(infoDot);
      info.appendChild(nameSpan);

      // Meta
      var meta = document.createElement("div");
      meta.className = "cam-card-meta";
      var ipSpan = document.createElement("span");
      var ipIcon = document.createElement("span");
      ipIcon.className = "material-symbols-outlined";
      ipIcon.textContent = "lan";
      ipSpan.appendChild(ipIcon);
      ipSpan.appendChild(document.createTextNode(" " + cam.ip + ":" + cam.port));
      meta.appendChild(ipSpan);
      if(cam.location){
        var locSpan = document.createElement("span");
        var locIcon = document.createElement("span");
        locIcon.className = "material-symbols-outlined";
        locIcon.textContent = "location_on";
        locSpan.appendChild(locIcon);
        locSpan.appendChild(document.createTextNode(" " + cam.location));
        meta.appendChild(locSpan);
      }
      if(cam.tags && cam.tags.length){
        var tagsSpan = document.createElement("span");
        cam.tags.forEach(function(t){
          var tag = document.createElement("span");
          tag.className = "cam-tag";
          tag.textContent = t;
          tagsSpan.appendChild(tag);
        });
        meta.appendChild(tagsSpan);
      }

      // Actions
      var actions = document.createElement("div");
      actions.className = "cam-card-actions";
      var btns = [
        {quick:"capture", icon:"photo_camera", title:"Capturer"},
        {quick:"wiper", icon:"water_drop", title:"Essuie-glace"}
      ];
      btns.forEach(function(b){
        var btn = document.createElement("button");
        btn.className = "btn-icon";
        btn.dataset.quick = b.quick;
        btn.title = b.title;
        var ic = document.createElement("span");
        ic.className = "material-symbols-outlined";
        ic.textContent = b.icon;
        btn.appendChild(ic);
        actions.appendChild(btn);
      });
      var spacer = document.createElement("div");
      spacer.style.flex = "1";
      actions.appendChild(spacer);
      var editBtn = document.createElement("button");
      editBtn.className = "btn-icon";
      editBtn.dataset.quick = "edit";
      editBtn.title = "Modifier";
      var editIc = document.createElement("span");
      editIc.className = "material-symbols-outlined";
      editIc.textContent = "edit";
      editBtn.appendChild(editIc);
      actions.appendChild(editBtn);
      var delBtn = document.createElement("button");
      delBtn.className = "btn-icon";
      delBtn.dataset.quick = "delete";
      delBtn.title = "Supprimer";
      delBtn.style.color = "var(--danger)";
      var delIc = document.createElement("span");
      delIc.className = "material-symbols-outlined";
      delIc.textContent = "delete";
      delBtn.appendChild(delIc);
      actions.appendChild(delBtn);

      card.appendChild(preview);
      card.appendChild(info);
      card.appendChild(meta);
      card.appendChild(actions);
      grid.appendChild(card);

      // -- Events --

      // Thumbnail load/error
      thumb.addEventListener("load", function(){
        thumb.style.display = "block";
        placeholder.style.display = "none";
      });
      thumb.addEventListener("error", function(){
        thumb.style.display = "none";
        placeholder.style.display = "";
      });

      // Click preview -> viewer
      preview.addEventListener("click", function(e){
        if(thumb.style.display !== "none"){
          e.stopPropagation();
          openViewer(thumb.src, cam.name);
        }
      });

      // Click card body -> detail panel
      card.addEventListener("click", function(e){
        if(e.target.closest(".cam-card-actions")) return;
        if(e.target.closest(".cam-card-preview")) return;
        openDetail(cam._id);
      });

      // Quick actions
      $$("[data-quick]", card).forEach(function(btn){
        btn.addEventListener("click", function(e){
          e.stopPropagation();
          var action = btn.dataset.quick;
          if(action === "edit") return openModal(findCam(cam._id));
          if(action === "delete") return deleteCam(cam._id);
          if(action === "capture"){
            toast("Capture en cours...", "info");
            API.capture(cam._id).then(function(r){
              if(r.ok) return r.blob();
              return r.json().then(function(j){ throw new Error(j.error || "Capture echouee"); });
            }).then(function(blob){
              var url = URL.createObjectURL(blob);
              updateCardThumb(cam._id, url);
              toast("Capture reussie", "success");
            }).catch(function(err){
              var msg = (err && err.message) ? err.message : "Camera injoignable";
              if(msg.indexOf("timed out") !== -1 || msg.indexOf("Timeout") !== -1) msg = "Camera injoignable (timeout)";
              toast(msg, "error");
            });
            return;
          }
          if(action === "wiper"){
            API.action(cam._id, {action: "wiper"}).then(function(res){
              if(res.error) toast(res.error, "error");
              else toast("Essuie-glace active", "success");
            }).catch(function(){ toast("Erreur", "error"); });
            return;
          }
        });
      });
    });
  }

  function updateCardThumb(id, url){
    var card = $('.cam-card[data-id="'+id+'"]');
    if(!card) return;
    var thumb = $(".cam-thumb", card);
    var placeholder = $(".cam-placeholder", card);
    if(thumb){
      thumb.src = url;
      thumb.style.display = "block";
      if(placeholder) placeholder.style.display = "none";
    }
  }

  function findCam(id){
    for(var i=0;i<_cameras.length;i++){
      if(_cameras[i]._id === id) return _cameras[i];
    }
    return null;
  }

  /* ================================================================
     Status polling (desactive pour le moment)
     ================================================================ */
  function pollStatus(){
    _cameras.forEach(function(cam){
      if(!cam.enabled) return;
      API.status(cam._id).then(function(res){
        _statusCache[cam._id] = res;
        updateStatusDots(cam._id, res.online);
      }).catch(function(){
        _statusCache[cam._id] = {online:false, device_info:{}, ptz:null};
        updateStatusDots(cam._id, false);
      });
    });
  }

  function updateStatusDots(id, online){
    var card = $('.cam-card[data-id="'+id+'"]');
    if(!card) return;
    var dots = $$(".cam-status", card);
    dots.forEach(function(dot){
      dot.className = "cam-status " + (online ? "online" : "offline");
    });
    var badge = $(".cam-card-badge", card);
    if(badge){
      var statusDot = $(".cam-status", badge);
      badge.textContent = "";
      if(statusDot) badge.appendChild(statusDot);
      badge.appendChild(document.createTextNode(" " + (online ? "En ligne" : "Hors ligne")));
    }
  }

  function startStatusPolling(){
    if(_statusTimer) clearInterval(_statusTimer);
    pollStatus();
    _statusTimer = setInterval(pollStatus, 60000);
  }

  /* ================================================================
     Fullscreen image viewer (pinch-to-zoom, touch-friendly)
     ================================================================ */
  var _viewer = null;

  function initViewer(){
    var el = document.createElement("div");
    el.id = "cam-viewer";

    var header = document.createElement("div");
    header.className = "cam-viewer-header";
    var titleSpan = document.createElement("span");
    titleSpan.className = "cam-viewer-title";
    var closeBtn = document.createElement("button");
    closeBtn.className = "cam-viewer-close";
    closeBtn.setAttribute("aria-label", "Fermer");
    var closeIc = document.createElement("span");
    closeIc.className = "material-symbols-outlined";
    closeIc.textContent = "close";
    closeBtn.appendChild(closeIc);
    header.appendChild(titleSpan);
    header.appendChild(closeBtn);

    var body = document.createElement("div");
    body.className = "cam-viewer-body";
    var img = document.createElement("img");
    img.className = "cam-viewer-img";
    img.draggable = false;
    img.alt = "";
    body.appendChild(img);

    el.appendChild(header);
    el.appendChild(body);
    document.body.appendChild(el);

    // Zoom & pan state
    var scale = 1, posX = 0, posY = 0;
    var startDist = 0, startScale = 1;
    var dragging = false, dragStart = {x:0, y:0};

    function applyTransform(){
      img.style.transform = "translate("+posX+"px,"+posY+"px) scale("+scale+")";
    }

    function resetView(){
      scale = 1; posX = 0; posY = 0;
      applyTransform();
    }

    // Close
    closeBtn.addEventListener("click", closeViewer);
    el.addEventListener("click", function(e){
      if(e.target === el || e.target === body) closeViewer();
    });

    // Double-tap / double-click to toggle zoom
    var lastTap = 0;
    body.addEventListener("click", function(e){
      if(e.target !== img && e.target !== body) return;
      var now = Date.now();
      if(now - lastTap < 300){
        if(scale > 1.1){ resetView(); }
        else { scale = 3; posX = 0; posY = 0; applyTransform(); }
      }
      lastTap = now;
    });

    // Mouse wheel zoom
    body.addEventListener("wheel", function(e){
      e.preventDefault();
      var delta = e.deltaY > 0 ? 0.85 : 1.18;
      scale = Math.min(Math.max(scale * delta, 0.5), 12);
      if(scale < 1.05){ posX = 0; posY = 0; }
      applyTransform();
    }, {passive: false});

    // Mouse drag
    body.addEventListener("mousedown", function(e){
      if(scale <= 1.05 || e.button !== 0) return;
      dragging = true;
      dragStart = {x: e.clientX - posX, y: e.clientY - posY};
      body.style.cursor = "grabbing";
      e.preventDefault();
    });
    window.addEventListener("mousemove", function(e){
      if(!dragging) return;
      posX = e.clientX - dragStart.x;
      posY = e.clientY - dragStart.y;
      applyTransform();
    });
    window.addEventListener("mouseup", function(){
      dragging = false;
      body.style.cursor = "";
    });

    // Touch: pinch-to-zoom + drag
    var activeTouches = {};
    body.addEventListener("touchstart", function(e){
      for(var i=0;i<e.changedTouches.length;i++){
        activeTouches[e.changedTouches[i].identifier] = {
          x: e.changedTouches[i].clientX,
          y: e.changedTouches[i].clientY
        };
      }
      var keys = Object.keys(activeTouches);
      if(keys.length === 2){
        var t = keys.map(function(k){ return activeTouches[k]; });
        startDist = Math.hypot(t[1].x - t[0].x, t[1].y - t[0].y);
        startScale = scale;
      } else if(keys.length === 1 && scale > 1.05){
        dragging = true;
        dragStart = {x: activeTouches[keys[0]].x - posX, y: activeTouches[keys[0]].y - posY};
      }
    }, {passive: true});

    body.addEventListener("touchmove", function(e){
      var keys = Object.keys(activeTouches);
      for(var i=0;i<e.changedTouches.length;i++){
        var t = e.changedTouches[i];
        if(activeTouches[t.identifier]){
          activeTouches[t.identifier] = {x: t.clientX, y: t.clientY};
        }
      }
      if(keys.length >= 2){
        e.preventDefault();
        var pts = keys.map(function(k){ return activeTouches[k]; });
        var dist = Math.hypot(pts[1].x - pts[0].x, pts[1].y - pts[0].y);
        scale = Math.min(Math.max(startScale * (dist / startDist), 0.5), 12);
        applyTransform();
      } else if(keys.length === 1 && dragging){
        e.preventDefault();
        posX = activeTouches[keys[0]].x - dragStart.x;
        posY = activeTouches[keys[0]].y - dragStart.y;
        applyTransform();
      }
    }, {passive: false});

    body.addEventListener("touchend", function(e){
      for(var i=0;i<e.changedTouches.length;i++){
        delete activeTouches[e.changedTouches[i].identifier];
      }
      if(Object.keys(activeTouches).length < 2) dragging = false;
      if(scale < 1.05){ posX = 0; posY = 0; applyTransform(); }
    }, {passive: true});

    _viewer = {overlay: el, img: img, title: titleSpan, reset: resetView};
  }

  function openViewer(src, title){
    if(!_viewer) initViewer();
    _viewer.img.src = src;
    _viewer.title.textContent = title || "";
    _viewer.reset();
    _viewer.overlay.classList.add("show");
    document.body.style.overflow = "hidden";
  }

  function closeViewer(){
    if(!_viewer) return;
    _viewer.overlay.classList.remove("show");
    document.body.style.overflow = "";
  }

  /* ================================================================
     CRUD Modal
     ================================================================ */
  var _editId = null;

  function openModal(cam){
    _editId = cam ? cam._id : null;
    var title = cam ? "Modifier la camera" : "Nouvelle camera";
    $("#cam-modal-title").textContent = title;

    var form = $("#cam-form");
    form.reset();
    $("#test-result").className = "cam-test-result";
    $("#test-result").textContent = "";

    if(cam){
      form.querySelector('[name="_id"]').value = cam._id;
      form.querySelector('[name="name"]').value = cam.name || "";
      form.querySelector('[name="ip"]').value = cam.ip || "";
      form.querySelector('[name="port"]').value = cam.port || 80;
      form.querySelector('[name="channel"]').value = cam.channel || 1;
      form.querySelector('[name="user"]').value = cam.user || "admin";
      form.querySelector('[name="password"]').value = "";
      form.querySelector('[name="protocol"]').value = cam.protocol || "http";
      form.querySelector('[name="brand"]').value = cam.brand || "hikvision";
      form.querySelector('[name="location"]').value = cam.location || "";
      form.querySelector('[name="tags"]').value = (cam.tags||[]).join(", ");
      form.querySelector('[name="enabled"]').checked = cam.enabled !== false;
    }

    $("#cam-modal-backdrop").classList.add("show");
  }

  function closeModal(){
    $("#cam-modal-backdrop").classList.remove("show");
    _editId = null;
  }

  function saveCamera(){
    var form = $("#cam-form");
    var name = form.querySelector('[name="name"]').value.trim();
    var ip = form.querySelector('[name="ip"]').value.trim();
    if(!name || !ip){ toast("Nom et IP requis", "error"); return; }

    var data = {
      name: name,
      ip: ip,
      port: parseInt(form.querySelector('[name="port"]').value) || 80,
      channel: parseInt(form.querySelector('[name="channel"]').value) || 1,
      user: form.querySelector('[name="user"]').value.trim() || "admin",
      password: form.querySelector('[name="password"]').value,
      protocol: form.querySelector('[name="protocol"]').value,
      brand: form.querySelector('[name="brand"]').value,
      location: form.querySelector('[name="location"]').value.trim(),
      tags: form.querySelector('[name="tags"]').value,
      enabled: form.querySelector('[name="enabled"]').checked
    };

    var promise = _editId ? API.update(_editId, data) : API.create(data);
    promise.then(function(res){
      if(res.error){ toast(res.error, "error"); return; }
      closeModal();
      toast(_editId ? "Camera modifiee" : "Camera ajoutee", "success");
      loadAll();
    }).catch(function(){ toast("Erreur serveur", "error"); });
  }

  function deleteCam(id){
    var cam = findCam(id);
    var name = cam ? cam.name : id;
    if(!confirm("Supprimer la camera \""+name+"\" ?")) return;
    API.remove(id).then(function(res){
      if(res.ok){
        toast("Camera supprimee", "success");
        if(_currentDetail === id) closeDetail();
        loadAll();
      } else { toast("Echec", "error"); }
    }).catch(function(){ toast("Erreur serveur", "error"); });
  }

  /* ================================================================
     Detail panel
     ================================================================ */
  function openDetail(id){
    _currentDetail = id;
    var cam = findCam(id);
    if(!cam) return;

    var overlay = $("#cam-detail-overlay");
    overlay.style.display = "block";
    overlay.offsetHeight;
    overlay.classList.add("show");

    $("#detail-title").textContent = cam.name;
    $("#detail-status").className = "cam-status unknown";

    // Afficher la derniere capture si disponible
    var img = $("#detail-snap-img");
    img.src = "/api/cameras/"+encodeURIComponent(id)+"/snapshot";
    img.style.display = "none";
    img.onload = function(){ img.style.display = "block"; $("#detail-snap-ph").style.display = "none"; };
    img.onerror = function(){ img.style.display = "none"; $("#detail-snap-ph").style.display = ""; };
    $("#detail-snap-ph").style.display = "";

    renderDeviceInfo({});
  }

  function closeDetail(){
    var overlay = $("#cam-detail-overlay");
    overlay.classList.remove("show");
    setTimeout(function(){ overlay.style.display = "none"; }, 300);
    _currentDetail = null;
  }

  function captureForDetail(id){
    API.capture(id).then(function(r){
      if(!r.ok) throw new Error("Capture failed");
      return r.blob();
    }).then(function(blob){
      var url = URL.createObjectURL(blob);
      var img = $("#detail-snap-img");
      img.src = url;
      img.style.display = "block";
      $("#detail-snap-ph").style.display = "none";
      updateCardThumb(id, url);
    }).catch(function(){
      // Keep placeholder
    });
  }

  function renderDeviceInfo(info){
    var container = $("#detail-device-info");
    var keys = Object.keys(info).filter(function(k){ return k[0] !== "_"; });
    if(!keys.length){
      container.textContent = "";
      var span = document.createElement("span");
      span.style.fontSize = ".82rem";
      span.style.color = "var(--muted)";
      span.textContent = "Informations indisponibles";
      container.appendChild(span);
      return;
    }
    var priority = ["deviceName","model","Model","serialNumber","SerialNumber","firmwareVersion","FirmwareVersion","macAddress","deviceType","Manufacturer"];
    var sorted = priority.filter(function(k){ return info[k]; })
      .concat(keys.filter(function(k){ return priority.indexOf(k)===-1; }));

    container.textContent = "";
    sorted.slice(0,8).forEach(function(k){
      var item = document.createElement("div");
      item.className = "device-info-item";
      var label = document.createElement("div");
      label.className = "label";
      label.textContent = k;
      var value = document.createElement("div");
      value.className = "value";
      value.textContent = String(info[k]);
      item.appendChild(label);
      item.appendChild(value);
      container.appendChild(item);
    });
  }

  /* ================================================================
     Event bindings
     ================================================================ */
  function initEvents(){
    $("#btn-add-cam").addEventListener("click", function(){ openModal(null); });

    $("#btn-import-json").addEventListener("click", function(){
      if(!confirm("Importer les cameras depuis hik_cameras.json ?\nLes cameras deja presentes (meme IP+port) seront ignorees.")) return;
      fetch("/api/cameras/import-json", {method:"POST", headers:{"X-CSRFToken": CSRF}})
        .then(function(r){ return r.json(); })
        .then(function(res){
          if(res.error){ toast(res.error, "error"); return; }
          toast(res.imported + " cameras importees, " + res.skipped + " ignorees", "success");
          loadAll();
        })
        .catch(function(){ toast("Erreur import", "error"); });
    });

    $$("[data-close]", $("#cam-modal-backdrop")).forEach(function(el){
      el.addEventListener("click", function(e){ e.preventDefault(); closeModal(); });
    });
    $("#cam-modal-backdrop").addEventListener("click", function(e){
      if(e.target === this) closeModal();
    });

    $("#btn-save-cam").addEventListener("click", function(e){ e.preventDefault(); saveCamera(); });

    $("#btn-test-conn").addEventListener("click", function(e){
      e.preventDefault();
      var form = $("#cam-form");
      var result = $("#test-result");
      result.className = "cam-test-result";
      result.textContent = "";

      var data = {
        ip: form.querySelector('[name="ip"]').value.trim(),
        port: parseInt(form.querySelector('[name="port"]').value) || 80,
        user: form.querySelector('[name="user"]').value.trim() || "admin",
        password: form.querySelector('[name="password"]').value,
        channel: parseInt(form.querySelector('[name="channel"]').value) || 1,
        protocol: form.querySelector('[name="protocol"]').value,
        brand: form.querySelector('[name="brand"]').value
      };

      if(!data.ip){
        result.className = "cam-test-result err";
        result.textContent = "IP requise";
        return;
      }

      var loading = document.createElement("span");
      loading.className = "cam-loading";
      result.textContent = "";
      result.appendChild(loading);
      result.appendChild(document.createTextNode(" Test en cours..."));
      result.className = "cam-test-result ok";
      result.style.display = "block";

      var handler = (_editId && !data.password)
        ? API.testConn(_editId)
        : API.testNew(data);

      handler.then(function(res){
        if(res.ok){
          result.className = "cam-test-result ok";
          var model = (res.info||{}).model || (res.info||{}).Model || (res.info||{}).deviceName || "OK";
          result.textContent = "Connexion reussie - " + model;
        } else {
          result.className = "cam-test-result err";
          result.textContent = "Echec: " + (res.error||"Erreur inconnue");
        }
      }).catch(function(){
        result.className = "cam-test-result err";
        result.textContent = "Erreur reseau";
      });
    });

    $$(".pw-toggle").forEach(function(btn){
      btn.addEventListener("click", function(){
        var input = btn.parentNode.querySelector("input");
        var icon = btn.querySelector(".material-symbols-outlined");
        if(input.type === "password"){
          input.type = "text";
          icon.textContent = "visibility_off";
        } else {
          input.type = "password";
          icon.textContent = "visibility";
        }
      });
    });

    $("#detail-close-btn").addEventListener("click", closeDetail);
    $("#cam-detail-overlay").addEventListener("click", function(e){
      if(e.target === this) closeDetail();
    });

    $("#detail-capture-btn").addEventListener("click", function(){
      if(_currentDetail) captureForDetail(_currentDetail);
    });

    $("#detail-wiper-btn").addEventListener("click", function(){
      if(!_currentDetail) return;
      API.action(_currentDetail, {action: "wiper"}).then(function(res){
        if(res.error) toast(res.error, "error");
        else toast("Essuie-glace active", "success");
      }).catch(function(){ toast("Erreur", "error"); });
    });

    $("#detail-edit-btn").addEventListener("click", function(){
      if(_currentDetail){
        var cam = findCam(_currentDetail);
        if(cam){
          closeDetail();
          setTimeout(function(){ openModal(cam); }, 350);
        }
      }
    });

    // Detail snapshot click -> open viewer
    $("#detail-snap-img").addEventListener("click", function(){
      if(this.style.display !== "none" && _currentDetail){
        var cam = findCam(_currentDetail);
        openViewer(this.src, cam ? cam.name : "");
      }
    });

    // Keyboard: Escape closes panels (viewer > modal > detail)
    document.addEventListener("keydown", function(e){
      if(e.key === "Escape"){
        if(_viewer && _viewer.overlay.classList.contains("show")) closeViewer();
        else if($("#cam-modal-backdrop").classList.contains("show")) closeModal();
        else if($("#cam-detail-overlay").classList.contains("show")) closeDetail();
      }
    });
  }

  /* ================================================================
     Init
     ================================================================ */
  function loadAll(){
    API.list().then(function(cameras){
      renderGrid(cameras);
    }).catch(function(err){
      console.error("Failed to load cameras", err);
      toast("Erreur de chargement", "error");
    });
  }

  initEvents();
  loadAll();

})();
