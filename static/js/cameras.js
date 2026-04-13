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
    presets:    function(id){ return fetch("/api/cameras/"+id+"/presets").then(function(r){ return r.json(); }); },
    ptz:        function(id,d){ return fetch("/api/cameras/"+id+"/ptz", {method:"POST", headers:jsonHeaders(), body:JSON.stringify(d)}).then(function(r){ return r.json(); }); },
    gotoPreset: function(id,d){ return fetch("/api/cameras/"+id+"/preset", {method:"POST", headers:jsonHeaders(), body:JSON.stringify(d)}).then(function(r){ return r.json(); }); },
    action:     function(id,d){ return fetch("/api/cameras/"+id+"/action", {method:"POST", headers:jsonHeaders(), body:JSON.stringify(d)}).then(function(r){ return r.json(); }); },
    testConn:   function(id){ return fetch("/api/cameras/"+id+"/test", {method:"POST", headers:{"X-CSRFToken": CSRF}}).then(function(r){ return r.json(); }); },
    testNew:    function(d){ return fetch("/api/cameras/test-connection", {method:"POST", headers:jsonHeaders(), body:JSON.stringify(d)}).then(function(r){ return r.json(); }); }
  };

  /* ================================================================
     State
     ================================================================ */
  var _cameras = [];
  var _statusCache = {};   // cam_id -> {online, device_info, ptz}
  var _currentDetail = null; // cam id open in detail panel
  var _statusTimer = null;

  /* ================================================================
     Grid rendering
     ================================================================ */
  function renderGrid(cameras){
    _cameras = cameras || [];
    var grid = $("#camera-grid");
    var empty = $("#cam-empty");
    var countText = $("#cam-count-text");

    if(!_cameras.length){
      grid.innerHTML = "";
      empty.style.display = "block";
      if(countText) countText.textContent = "0 cameras";
      return;
    }
    empty.style.display = "none";
    if(countText) countText.textContent = _cameras.length + " camera" + (_cameras.length>1?"s":"");

    grid.innerHTML = _cameras.map(function(cam){
      var st = _statusCache[cam._id];
      var statusClass = st ? (st.online ? "online" : "offline") : "unknown";
      var statusLabel = st ? (st.online ? "En ligne" : "Hors ligne") : "...";
      var tags = (cam.tags||[]).map(function(t){ return '<span class="cam-tag">'+escHtml(t)+"</span>"; }).join(" ");

      return '<div class="cam-card'+(cam.enabled?"":" disabled")+'" data-id="'+cam._id+'">'
        + '<div class="cam-card-preview">'
        +   '<span class="material-symbols-outlined cam-placeholder">videocam</span>'
        +   '<div class="cam-card-overlay"></div>'
        +   '<div class="cam-card-badge"><span class="cam-status '+statusClass+'"></span> '+escHtml(statusLabel)+'</div>'
        + '</div>'
        + '<div class="cam-card-info">'
        +   '<span class="cam-status '+statusClass+'"></span>'
        +   '<span class="cam-card-name">'+escHtml(cam.name)+'</span>'
        + '</div>'
        + '<div class="cam-card-meta">'
        +   '<span><span class="material-symbols-outlined">lan</span> '+escHtml(cam.ip)+':'+cam.port+'</span>'
        +   (cam.location ? '<span><span class="material-symbols-outlined">location_on</span> '+escHtml(cam.location)+'</span>' : '')
        +   (tags ? '<span>'+tags+'</span>' : '')
        + '</div>'
        + '<div class="cam-card-actions">'
        +   '<button class="btn-icon" data-quick="capture" title="Capturer"><span class="material-symbols-outlined">photo_camera</span></button>'
        +   '<button class="btn-icon" data-quick="goto_home" title="Position Home"><span class="material-symbols-outlined">home</span></button>'
        +   '<button class="btn-icon" data-quick="wiper" title="Wiper"><span class="material-symbols-outlined">water_drop</span></button>'
        +   '<div style="flex:1;"></div>'
        +   '<button class="btn-icon" data-quick="edit" title="Modifier"><span class="material-symbols-outlined">edit</span></button>'
        +   '<button class="btn-icon" data-quick="delete" title="Supprimer" style="color:var(--danger);"><span class="material-symbols-outlined">delete</span></button>'
        + '</div>'
        + '</div>';
    }).join("");

    // Bind card events
    $$(".cam-card", grid).forEach(function(card){
      var id = card.dataset.id;

      // Click on card body opens detail
      card.addEventListener("click", function(e){
        if(e.target.closest(".cam-card-actions")) return;
        openDetail(id);
      });

      // Quick actions
      $$("[data-quick]", card).forEach(function(btn){
        btn.addEventListener("click", function(e){
          e.stopPropagation();
          var action = btn.dataset.quick;
          if(action === "edit") return openModal(findCam(id));
          if(action === "delete") return deleteCam(id);
          if(action === "capture"){
            toast("Capture en cours...", "info");
            API.capture(id).then(function(r){
              if(r.ok) return r.blob();
              throw new Error("Capture failed");
            }).then(function(blob){
              // Open snapshot in new tab
              var url = URL.createObjectURL(blob);
              window.open(url, "_blank");
              toast("Capture reussie", "success");
            }).catch(function(){ toast("Echec de la capture", "error"); });
            return;
          }
          // Generic quick action
          API.action(id, {action: action}).then(function(res){
            if(res.error) toast(res.error, "error");
            else toast(action + " OK", "success");
          }).catch(function(){ toast("Erreur", "error"); });
        });
      });
    });
  }

  function findCam(id){
    for(var i=0;i<_cameras.length;i++){
      if(_cameras[i]._id === id) return _cameras[i];
    }
    return null;
  }

  /* ================================================================
     Status polling
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
      var dot = $(".cam-status", badge);
      badge.innerHTML = "";
      if(dot) badge.appendChild(dot);
      badge.appendChild(document.createTextNode(" " + (online ? "En ligne" : "Hors ligne")));
    }
  }

  function startStatusPolling(){
    if(_statusTimer) clearInterval(_statusTimer);
    pollStatus();
    _statusTimer = setInterval(pollStatus, 60000);
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
    // Force reflow for animation
    overlay.offsetHeight;
    overlay.classList.add("show");

    // Header
    $("#detail-title").textContent = cam.name;
    $("#detail-status").className = "cam-status unknown";

    // Reset snapshot
    $("#detail-snap-img").style.display = "none";
    $("#detail-snap-ph").style.display = "";

    // Load status
    API.status(id).then(function(res){
      _statusCache[id] = res;
      var dot = $("#detail-status");
      dot.className = "cam-status " + (res.online ? "online" : "offline");
      updateStatusDots(id, res.online);

      // PTZ position
      if(res.ptz){
        $("#ptz-position").textContent = "Pan: "+res.ptz.azimuth.toFixed(1)
          +" | Tilt: "+res.ptz.elevation.toFixed(1)
          +" | Zoom: "+res.ptz.zoom.toFixed(1)+"x";
      }

      // Device info
      renderDeviceInfo(res.device_info || {});
    }).catch(function(){
      $("#detail-status").className = "cam-status offline";
      renderDeviceInfo({});
    });

    // Load presets
    renderPresets([]);
    API.presets(id).then(function(presets){
      renderPresets(presets || []);
    }).catch(function(){});

    // Try initial capture
    captureForDetail(id);
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
      var img = $("#detail-snap-img");
      img.src = URL.createObjectURL(blob);
      img.style.display = "block";
      $("#detail-snap-ph").style.display = "none";
    }).catch(function(){
      // Keep placeholder
    });
  }

  function renderPresets(presets){
    var container = $("#detail-presets");
    if(!presets.length){
      container.innerHTML = '<span style="font-size:.82rem;color:var(--muted);">Aucun preset configure</span>';
      return;
    }
    container.innerHTML = presets.map(function(p){
      return '<button class="preset-chip" data-preset="'+p.id+'">'
        + '<span class="material-symbols-outlined">bookmark</span> '
        + escHtml(p.name || ("Preset "+p.id))
        + '</button>';
    }).join("");

    $$(".preset-chip", container).forEach(function(chip){
      chip.addEventListener("click", function(){
        if(!_currentDetail) return;
        var pid = parseInt(chip.dataset.preset);
        API.gotoPreset(_currentDetail, {preset_id: pid}).then(function(res){
          if(res.error) toast(res.error, "error");
          else toast("Preset "+pid+" OK", "success");
          // Refresh snapshot after 2s (time for camera to move)
          setTimeout(function(){ captureForDetail(_currentDetail); }, 2000);
        });
      });
    });
  }

  function renderDeviceInfo(info){
    var container = $("#detail-device-info");
    // Filter out internal keys
    var keys = Object.keys(info).filter(function(k){ return k[0] !== "_"; });
    if(!keys.length){
      container.innerHTML = '<span style="font-size:.82rem;color:var(--muted);">Informations indisponibles</span>';
      return;
    }
    // Show the most relevant keys
    var priority = ["deviceName","model","Model","serialNumber","SerialNumber","firmwareVersion","FirmwareVersion","macAddress","deviceType","Manufacturer"];
    var sorted = priority.filter(function(k){ return info[k]; })
      .concat(keys.filter(function(k){ return priority.indexOf(k)===-1; }));

    container.innerHTML = sorted.slice(0,8).map(function(k){
      return '<div class="device-info-item">'
        + '<div class="label">'+escHtml(k)+'</div>'
        + '<div class="value">'+escHtml(String(info[k]))+'</div>'
        + '</div>';
    }).join("");
  }

  /* ================================================================
     PTZ Controls
     ================================================================ */
  function initPTZ(){
    // Direction buttons - continuous move on pointerdown, stop on pointerup
    $$(".ptz-btn[data-pan]").forEach(function(btn){
      function startMove(e){
        e.preventDefault();
        if(!_currentDetail) return;
        var pan = parseInt(btn.dataset.pan);
        var tilt = parseInt(btn.dataset.tilt);
        var zoom = parseInt(btn.dataset.zoom);
        API.ptz(_currentDetail, {action:"move", pan:pan, tilt:tilt, zoom:zoom});
      }
      function stopMove(e){
        e.preventDefault();
        if(!_currentDetail) return;
        API.ptz(_currentDetail, {action:"stop"});
      }
      btn.addEventListener("pointerdown", startMove);
      btn.addEventListener("pointerup", stopMove);
      btn.addEventListener("pointerleave", stopMove);
    });

    // Stop button
    var stopBtn = $("#ptz-stop");
    if(stopBtn){
      stopBtn.addEventListener("click", function(){
        if(!_currentDetail) return;
        API.ptz(_currentDetail, {action:"stop"});
      });
    }

    // Zoom slider
    var zoomSlider = $("#ptz-zoom");
    if(zoomSlider){
      var zoomTimer = null;
      zoomSlider.addEventListener("input", function(){
        if(!_currentDetail) return;
        var val = parseInt(zoomSlider.value);
        if(val === 0) {
          API.ptz(_currentDetail, {action:"stop"});
          return;
        }
        API.ptz(_currentDetail, {action:"move", pan:0, tilt:0, zoom:val});
      });
      zoomSlider.addEventListener("pointerup", function(){
        zoomSlider.value = 0;
        if(_currentDetail) API.ptz(_currentDetail, {action:"stop"});
      });
      zoomSlider.addEventListener("pointerleave", function(){
        zoomSlider.value = 0;
        if(_currentDetail) API.ptz(_currentDetail, {action:"stop"});
      });
    }
  }

  /* ================================================================
     Day/Night
     ================================================================ */
  function initDayNight(){
    $$(".daynight-btn").forEach(function(btn){
      btn.addEventListener("click", function(){
        if(!_currentDetail) return;
        var mode = btn.dataset.mode;
        $$(".daynight-btn").forEach(function(b){ b.classList.remove("active"); });
        btn.classList.add("active");
        API.action(_currentDetail, {action:"daynight", params:{mode:mode}}).then(function(res){
          if(res.error) toast(res.error, "error");
          else toast("Mode "+mode, "success");
        });
      });
    });
  }

  /* ================================================================
     Actions
     ================================================================ */
  function initActions(){
    $$(".action-btn[data-action]").forEach(function(btn){
      btn.addEventListener("click", function(){
        if(!_currentDetail) return;
        var action = btn.dataset.action;

        if(action === "reboot"){
          if(!confirm("Rebooter cette camera ?")) return;
          API.action(_currentDetail, {action:"reboot", confirm:true}).then(function(res){
            if(res.error) toast(res.error, "error");
            else toast("Reboot envoye", "success");
          });
          return;
        }

        API.action(_currentDetail, {action:action}).then(function(res){
          if(res.error) toast(res.error, "error");
          else toast(action.replace(/_/g," ") + " OK", "success");
        }).catch(function(){ toast("Erreur", "error"); });
      });
    });
  }

  /* ================================================================
     Event bindings
     ================================================================ */
  function initEvents(){
    // Add button
    $("#btn-add-cam").addEventListener("click", function(){ openModal(null); });

    // Modal close
    $$("[data-close]", $("#cam-modal-backdrop")).forEach(function(el){
      el.addEventListener("click", function(e){ e.preventDefault(); closeModal(); });
    });
    // Backdrop click closes modal
    $("#cam-modal-backdrop").addEventListener("click", function(e){
      if(e.target === this) closeModal();
    });

    // Save
    $("#btn-save-cam").addEventListener("click", function(e){ e.preventDefault(); saveCamera(); });

    // Test connection
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

      if(!data.ip){ result.className = "cam-test-result err"; result.textContent = "IP requise"; return; }

      result.innerHTML = '<span class="cam-loading"></span> Test en cours...';
      result.className = "cam-test-result ok";
      result.style.display = "block";

      // If editing and no password entered, use existing camera's test endpoint
      if(_editId && !data.password){
        API.testConn(_editId).then(function(res){
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
      } else {
        API.testNew(data).then(function(res){
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
      }
    });

    // Password toggle
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

    // Detail panel - close
    $("#detail-close-btn").addEventListener("click", closeDetail);
    $("#cam-detail-overlay").addEventListener("click", function(e){
      if(e.target === this) closeDetail();
    });

    // Detail - capture button
    $("#detail-capture-btn").addEventListener("click", function(){
      if(_currentDetail) captureForDetail(_currentDetail);
    });

    // Detail - edit button
    $("#detail-edit-btn").addEventListener("click", function(){
      if(_currentDetail){
        var cam = findCam(_currentDetail);
        if(cam){
          closeDetail();
          setTimeout(function(){ openModal(cam); }, 350);
        }
      }
    });

    // Keyboard: Escape closes panels
    document.addEventListener("keydown", function(e){
      if(e.key === "Escape"){
        if($("#cam-modal-backdrop").classList.contains("show")) closeModal();
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
      startStatusPolling();
    }).catch(function(err){
      console.error("Failed to load cameras", err);
      toast("Erreur de chargement", "error");
    });
  }

  initEvents();
  initPTZ();
  initDayNight();
  initActions();
  loadAll();

})();
