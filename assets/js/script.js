(function () {
  "use strict";

  const STORAGE_KEY = "finger-snap-todos-v1";

  function todayISO() {
    const d = new Date();
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, "0");
    const day = String(d.getDate()).padStart(2, "0");
    return y + "-" + m + "-" + day;
  }

  function newId() {
    if (typeof crypto !== "undefined" && crypto.randomUUID) {
      return crypto.randomUUID();
    }
    return "t-" + Date.now() + "-" + Math.random().toString(36).slice(2, 9);
  }

  function loadTodos() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return [];
      const parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) return [];
      return parsed.map(function (t) {
        return {
          id: t.id || newId(),
          title: typeof t.title === "string" ? t.title : "",
          done: !!t.done,
          dueDate: t.dueDate || todayISO(),
          createdAt: t.createdAt || new Date().toISOString(),
        };
      });
    } catch {
      return [];
    }
  }

  function saveTodos(todoList) {
    const clean = todoList.map(function (t) {
      return {
        id: t.id,
        title: t.title,
        done: !!t.done,
        dueDate: t.dueDate,
        createdAt: t.createdAt || new Date().toISOString(),
      };
    });
    localStorage.setItem(STORAGE_KEY, JSON.stringify(clean));
  }

  /* —— Clock + greeting —— */
  const greetingEl = document.getElementById("greeting");
  const clockEl = document.getElementById("clock");

  if (greetingEl && clockEl) {
    function updateGreeting() {
      const h = new Date().getHours();
      let msg = "Good evening, Eesa";
      if (h < 12) msg = "Good morning, Eesa";
      else if (h < 17) msg = "Good afternoon, Eesa.";
      greetingEl.textContent = msg;
    }

    function pad(n) {
      return n < 10 ? "0" + n : String(n);
    }

    function tick() {
      const d = new Date();
      const opts = { weekday: "long", year: "numeric", month: "long", day: "numeric" };
      const dateStr = d.toLocaleDateString(undefined, opts);
      const timeStr =
        pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds());
      clockEl.textContent = dateStr + " · " + timeStr;
    }

    updateGreeting();
    tick();
    setInterval(tick, 1000);
    setInterval(updateGreeting, 60 * 1000);
  }

  /* —— Todo + calendar —— */
  const form = document.getElementById("todo-form");
  const titleInput = document.getElementById("todo-title");
  const dueInput = document.getElementById("todo-due");
  const listEl = document.getElementById("todo-list");
  const emptyEl = document.getElementById("todo-empty");
  const filterHint = document.getElementById("todo-filter-hint");
  const clearFilterBtn = document.getElementById("cal-clear-filter");
  const calPrev = document.getElementById("cal-prev");
  const calNext = document.getElementById("cal-next");
  const calMonthLabel = document.getElementById("cal-month-label");
  const calGrid = document.getElementById("cal-grid");

  if (!form || !listEl) return;

  let todos = loadTodos();
  const editingIds = new Set();
  let viewYear = new Date().getFullYear();
  let viewMonth = new Date().getMonth();
  let selectedDateISO = null;

  if (dueInput && !dueInput.value) {
    dueInput.value = todayISO();
  }

  function todosForDay(iso) {
    return todos.filter(function (t) {
      return (t.dueDate || "").slice(0, 10) === iso;
    });
  }

  function renderCalendar() {
    if (!calGrid || !calMonthLabel) return;

    const first = new Date(viewYear, viewMonth, 1);
    const last = new Date(viewYear, viewMonth + 1, 0);
    const padStart = first.getDay();

    calMonthLabel.textContent = first.toLocaleDateString(undefined, {
      month: "long",
      year: "numeric",
    });

    calGrid.innerHTML = "";

    const todayStr = todayISO();

    for (let i = 0; i < padStart; i += 1) {
      const b = document.createElement("div");
      b.className = "cal-day muted";
      b.setAttribute("aria-hidden", "true");
      calGrid.appendChild(b);
    }

    for (let day = 1; day <= last.getDate(); day += 1) {
      const iso =
        viewYear +
        "-" +
        String(viewMonth + 1).padStart(2, "0") +
        "-" +
        String(day).padStart(2, "0");
      const dayTodos = todosForDay(iso);
      const openCount = dayTodos.filter(function (t) {
        return !t.done;
      }).length;
      const doneCount = dayTodos.length - openCount;

      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "cal-day";
      btn.dataset.date = iso;
      if (iso === todayStr) btn.classList.add("today");
      if (selectedDateISO === iso) btn.classList.add("selected");
      btn.setAttribute("aria-label", iso + ", " + dayTodos.length + " tasks");

      const num = document.createElement("span");
      num.textContent = String(day);
      btn.appendChild(num);

      const dots = document.createElement("span");
      dots.className = "cal-dots";
      for (let j = 0; j < Math.min(openCount, 3); j += 1) {
        const d = document.createElement("span");
        d.className = "cal-dot";
        dots.appendChild(d);
      }
      for (let j = 0; j < Math.min(doneCount, 2) && dots.children.length < 4; j += 1) {
        const d = document.createElement("span");
        d.className = "cal-dot done";
        dots.appendChild(d);
      }
      if (dayTodos.length > dots.children.length && dayTodos.length > 0) {
        const more = document.createElement("span");
        more.className = "cal-dot";
        more.style.opacity = "0.6";
        dots.appendChild(more);
      }
      btn.appendChild(dots);

      btn.addEventListener("click", function () {
        selectedDateISO = selectedDateISO === iso ? null : iso;
        updateFilterUI();
        renderCalendar();
        renderList();
      });

      calGrid.appendChild(btn);
    }
  }

  function updateFilterUI() {
    if (clearFilterBtn) {
      clearFilterBtn.hidden = !selectedDateISO;
    }
    if (filterHint) {
      if (selectedDateISO) {
        filterHint.hidden = false;
        filterHint.textContent = "Showing tasks due " + selectedDateISO + ".";
      } else {
        filterHint.hidden = true;
      }
    }
  }

  function visibleTodos() {
    if (!selectedDateISO) return todos.slice();
    return todos.filter(function (t) {
      return (t.dueDate || "").slice(0, 10) === selectedDateISO;
    });
  }

  function renderList() {
    const visible = visibleTodos();
    listEl.innerHTML = "";

    if (visible.length === 0) {
      if (emptyEl) {
        emptyEl.hidden = false;
        emptyEl.textContent = selectedDateISO
          ? "No tasks for this date."
          : "No tasks yet. Add one above.";
      }
      return;
    }

    if (emptyEl) emptyEl.hidden = true;

    visible
      .slice()
      .sort(function (a, b) {
        if (a.done !== b.done) return a.done ? 1 : -1;
        return (a.dueDate || "").localeCompare(b.dueDate || "");
      })
      .forEach(function (todo) {
        const li = document.createElement("li");
        li.className = "todo-item" + (todo.done ? " done" : "");
        li.dataset.id = todo.id;

        const cb = document.createElement("input");
        cb.type = "checkbox";
        cb.checked = !!todo.done;
        cb.setAttribute("aria-label", "Mark done");
        cb.addEventListener("change", function () {
          todo.done = cb.checked;
          saveTodos(todos);
          renderCalendar();
          renderList();
        });

        const body = document.createElement("div");
        body.className = "todo-body";

        const isEditing = editingIds.has(todo.id);

        const titleSpan = document.createElement("div");
        titleSpan.className = "todo-title-text";
        titleSpan.textContent = todo.title;
        titleSpan.style.display = isEditing ? "none" : "block";

        const titleInputEl = document.createElement("input");
        titleInputEl.type = "text";
        titleInputEl.className = "todo-title-input";
        titleInputEl.value = todo.title;
        titleInputEl.style.display = isEditing ? "block" : "none";
        titleInputEl.maxLength = 200;

        const meta = document.createElement("div");
        meta.className = "todo-meta";
        const dateLabel = document.createElement("label");
        dateLabel.setAttribute("for", "due-" + todo.id);
        dateLabel.textContent = "Due";
        const dateEl = document.createElement("input");
        dateEl.type = "date";
        dateEl.id = "due-" + todo.id;
        dateEl.value = (todo.dueDate || "").slice(0, 10) || todayISO();
        dateEl.addEventListener("change", function () {
          todo.dueDate = dateEl.value;
          saveTodos(todos);
          renderCalendar();
          renderList();
        });
        meta.appendChild(dateLabel);
        meta.appendChild(dateEl);

        body.appendChild(titleSpan);
        body.appendChild(titleInputEl);
        body.appendChild(meta);

        const actions = document.createElement("div");
        actions.className = "todo-actions";

        const editBtn = document.createElement("button");
        editBtn.type = "button";
        editBtn.textContent = isEditing ? "Save" : "Edit";
        editBtn.addEventListener("click", function () {
          if (editingIds.has(todo.id)) {
            const v = titleInputEl.value.trim();
            if (v) todo.title = v;
            editingIds.delete(todo.id);
            saveTodos(todos);
            renderList();
          } else {
            editingIds.add(todo.id);
            renderList();
            requestAnimationFrame(function () {
              const row = listEl.querySelector('[data-id="' + todo.id + '"]');
              const inp = row && row.querySelector(".todo-title-input");
              if (inp) {
                inp.focus();
                inp.select();
              }
            });
          }
        });

        const delBtn = document.createElement("button");
        delBtn.type = "button";
        delBtn.className = "danger";
        delBtn.textContent = "Delete";
        delBtn.addEventListener("click", function () {
          editingIds.delete(todo.id);
          todos = todos.filter(function (t) {
            return t.id !== todo.id;
          });
          saveTodos(todos);
          renderCalendar();
          renderList();
        });

        actions.appendChild(editBtn);
        actions.appendChild(delBtn);

        li.appendChild(cb);
        li.appendChild(body);
        li.appendChild(actions);

        titleInputEl.addEventListener("keydown", function (e) {
          if (e.key === "Enter") {
            e.preventDefault();
            editBtn.click();
          }
        });

        listEl.appendChild(li);
      });
  }

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    const title = titleInput.value.trim();
    if (!title) return;
    const due = dueInput && dueInput.value ? dueInput.value : todayISO();
    todos.push({
      id: newId(),
      title: title,
      done: false,
      dueDate: due,
      createdAt: new Date().toISOString(),
    });
    titleInput.value = "";
    saveTodos(todos);
    renderCalendar();
    renderList();
  });

  if (clearFilterBtn) {
    clearFilterBtn.addEventListener("click", function () {
      selectedDateISO = null;
      updateFilterUI();
      renderCalendar();
      renderList();
    });
  }

  if (calPrev) {
    calPrev.addEventListener("click", function () {
      viewMonth -= 1;
      if (viewMonth < 0) {
        viewMonth = 11;
        viewYear -= 1;
      }
      renderCalendar();
    });
  }

  if (calNext) {
    calNext.addEventListener("click", function () {
      viewMonth += 1;
      if (viewMonth > 11) {
        viewMonth = 0;
        viewYear += 1;
      }
      renderCalendar();
    });
  }

  renderCalendar();
  updateFilterUI();
  renderList();
})();
