document.addEventListener("DOMContentLoaded", function () {
	const csrfMeta = document.querySelector('meta[name="csrf-token"]');
	if (csrfMeta) document.querySelectorAll("form[method='post'], form[method='POST']").forEach(function (form) {
		if (!form.querySelector('input[name="csrf_token"]')) {
			const csrfField = document.createElement("input");
			csrfField.type = "hidden";
			csrfField.name = "csrf_token";
			csrfField.value = csrfMeta.content;
			form.appendChild(csrfField);
		}
	});
	const passwordToggle = document.getElementById("passwordToggle");
	const passwordInput = document.getElementById("password");
	if (passwordToggle && passwordInput) passwordToggle.addEventListener("click", function () {
		const visible = passwordInput.type === "text";
		passwordInput.type = visible ? "password" : "text";
		passwordToggle.setAttribute("aria-pressed", String(!visible));
		passwordToggle.setAttribute("aria-label", visible ? "Show password" : "Hide password");
		const icon = passwordToggle.querySelector("i");
		if (icon) icon.className = visible ? "bi bi-eye" : "bi bi-eye-slash";
	});
	const loginForm = document.getElementById("loginForm");
	const loginSubmit = document.getElementById("loginSubmit");
	if (loginForm && loginSubmit) loginForm.addEventListener("submit", function () {
		if (loginSubmit.disabled) return;
		loginSubmit.disabled = true;
		loginSubmit.classList.add("is-loading");
	});
	const body = document.body;
	const sidebar = document.getElementById("appSidebar");
	const sidebarCollapse = document.getElementById("sidebarCollapse");
	const sidebarScrim = document.getElementById("sidebarScrim");
	const savedSidebar = localStorage.getItem("sidebarCollapsed") || localStorage.getItem("adufarms-sidebar");
	if (savedSidebar === "true" || savedSidebar === "collapsed") {
		if (window.matchMedia("(min-width: 901px)").matches) body.classList.add("sidebar-collapsed");
	}
	if (sidebarCollapse) sidebarCollapse.addEventListener("click", function () {
		if (window.matchMedia("(max-width: 900px)").matches) { body.classList.toggle("sidebar-open"); return; }
		body.classList.toggle("sidebar-collapsed");
		const collapsed = body.classList.contains("sidebar-collapsed");
		localStorage.setItem("sidebarCollapsed", String(collapsed));
		localStorage.setItem("adufarms-sidebar", collapsed ? "collapsed" : "expanded");
		const icon = sidebarCollapse.querySelector("i");
		if (icon) icon.className = body.classList.contains("sidebar-collapsed") ? "bi bi-layout-sidebar" : "bi bi-layout-sidebar-inset";
	});
	const savedTheme = localStorage.getItem("adufarms-theme");
	if (savedTheme) body.dataset.theme = savedTheme;
	const themeToggle = document.getElementById("themeToggle");
	if (themeToggle) themeToggle.addEventListener("click", function () {
		const nextTheme = body.dataset.theme === "dark" ? "light" : "dark";
		body.dataset.theme = nextTheme;
		localStorage.setItem("adufarms-theme", nextTheme);
		const icon = themeToggle.querySelector("i");
		if (icon) icon.className = nextTheme === "dark" ? "bi bi-sun" : "bi bi-moon-stars";
	});
	const menuToggle = document.getElementById("mobileMenuToggle");
	if (menuToggle) menuToggle.addEventListener("click", function () { body.classList.toggle("sidebar-open"); });
	if (sidebarScrim) sidebarScrim.addEventListener("click", function () { body.classList.remove("sidebar-open"); });
	if (sidebar) sidebar.querySelectorAll(".sidebar-nav a").forEach(function (link) {
		const label = link.querySelector("span");
		if (label) link.title = label.textContent.trim();
	});
	document.querySelectorAll(".app-sidebar a").forEach(function (link) { link.addEventListener("click", function () { body.classList.remove("sidebar-open"); }); });
	const popoverPairs = [["notificationToggle", "notificationPanel"], ["profileToggle", "profilePanel"], ["newTransactionToggle", "newTransactionPanel"], ["sidebarProfileToggle", "sidebarProfilePanel"]];
	const closePopovers = function () {
		document.querySelectorAll(".popover-panel.open, .sidebar-popover.open").forEach(function (panel) {
			panel.classList.remove("open");
			const toggle = document.querySelector('[aria-controls="' + panel.id + '"]');
			if (toggle) toggle.setAttribute("aria-expanded", "false");
		});
	};
	popoverPairs.forEach(function (pair) {
		const toggle = document.getElementById(pair[0]);
		const panel = document.getElementById(pair[1]);
		if (!toggle || !panel) return;
		toggle.addEventListener("click", function (event) {
			event.stopPropagation();
			closePopovers();
			panel.classList.toggle("open");
			toggle.setAttribute("aria-expanded", panel.classList.contains("open"));
		});
		panel.addEventListener("click", function (event) { event.stopPropagation(); });
	});
	document.addEventListener("click", closePopovers);
	document.addEventListener("keydown", function (event) { if (event.key === "Escape") { body.classList.remove("sidebar-open"); closePopovers(); } });

	const modalElement = document.getElementById("actionConfirmModal");
	if (modalElement && typeof bootstrap !== "undefined") {
		const modal = new bootstrap.Modal(modalElement);
		const message = document.getElementById("actionConfirmMessage");
		const confirmButton = document.getElementById("actionConfirmButton");
		const reversalReason = document.getElementById("reversalReason");
		let pendingForm = null;
		document.querySelectorAll("form[onsubmit]").forEach(function (form) {
			const inlineHandler = form.getAttribute("onsubmit") || "";
			if (inlineHandler.includes("confirm")) {
				form.removeAttribute("onsubmit");
				form.dataset.confirm = form.action.includes("/restore") ? "Restore this transaction?" : "Are you sure you want to reverse this transaction?";
			}
		});
		document.querySelectorAll("form[data-confirm]").forEach(function (form) {
			form.addEventListener("submit", function (event) {
				if (form.dataset.confirmed === "true") { form.dataset.confirmed = "false"; return; }
				event.preventDefault(); pendingForm = form; message.textContent = form.dataset.confirm; if (reversalReason) { reversalReason.value = ""; reversalReason.classList.remove("is-invalid"); } modal.show();
			});
		});
		if (confirmButton) confirmButton.addEventListener("click", function () {
			if (!pendingForm) return;
			const reason = reversalReason ? reversalReason.value.trim() : "";
			if (!reason) { if (reversalReason) { reversalReason.classList.add("is-invalid"); reversalReason.focus(); } return; }
			let field = pendingForm.querySelector('input[name="reason"]');
			if (!field) { field = document.createElement("input"); field.type = "hidden"; field.name = "reason"; pendingForm.appendChild(field); }
			field.value = reason; pendingForm.dataset.confirmed = "true"; modal.hide(); pendingForm.requestSubmit(); pendingForm = null;
		});
	}
	const deleteModalElement = document.getElementById("deleteTransactionModal");
	const deleteForm = document.getElementById("deleteTransactionForm");
	if (deleteModalElement && deleteForm) {
		deleteModalElement.addEventListener("show.bs.modal", function (event) {
			const trigger = event.relatedTarget;
			if (!trigger) return;
			deleteForm.action = trigger.dataset.deleteUrl;
			const type = trigger.dataset.deleteType || "Transaction";
			const rowCells = trigger.closest("tr") ? trigger.closest("tr").children : [];
			const cellText = function (index) { return rowCells[index] ? rowCells[index].textContent.trim() : "-"; };
			const title = document.getElementById("deleteTransactionTitle");
			const messageText = document.getElementById("deleteTransactionMessage");
			const submitText = document.getElementById("deleteTransactionSubmit");
			if (title) title.textContent = "Delete " + type + "?";
			if (messageText) messageText.textContent = "This action cannot be undone. Are you sure you want to delete this " + type.toLowerCase() + "?";
			if (submitText) submitText.textContent = "Delete " + type;
			const fields = { Id: "deleteId", Type: "deleteType", Party: "deleteParty", Quantity: "deleteQuantity", Amount: "deleteAmount", Date: "deleteDate" };
			Object.keys(fields).forEach(function (field) {
				const target = document.getElementById("deleteTransaction" + field);
				if (target) target.textContent = trigger.dataset[fields[field]] || "-";
			});
			const paid = document.getElementById("deleteTransactionPaid");
			const balance = document.getElementById("deleteTransactionBalance");
			const method = document.getElementById("deleteTransactionMethod");
			if (type === "Sale") {
				if (paid) paid.textContent = cellText(6);
				if (balance) balance.textContent = cellText(7);
				if (method) method.textContent = "-";
			} else if (type === "Payment") {
				if (paid) paid.textContent = "-";
				if (balance) balance.textContent = "-";
				if (method) method.textContent = cellText(4);
			} else {
				if (paid) paid.textContent = "-";
				if (balance) balance.textContent = "-";
				if (method) method.textContent = "-";
			}
			const reason = document.getElementById("deleteReason");
			if (reason) { reason.value = ""; reason.classList.remove("is-invalid"); }
		});
		deleteForm.addEventListener("submit", function (event) {
			const reason = document.getElementById("deleteReason");
			if (!reason || !reason.value.trim()) { event.preventDefault(); reason.classList.add("is-invalid"); reason.focus(); }
		});
	}
	document.querySelectorAll("form").forEach(function (form) {
		form.addEventListener("submit", function () {
			const button = form.querySelector("button[type='submit'], button:not([type])");
			if (!button || form.dataset.confirmed === "true") return;
			button.disabled = true;
			button.dataset.originalText = button.innerHTML;
			button.innerHTML = '<span class="spinner-border spinner-border-sm me-1" aria-hidden="true"></span> Processing...';
		});
	});
	document.querySelectorAll(".app-alerts .alert").forEach(function (alert) {
		window.setTimeout(function () { if (typeof bootstrap !== "undefined") bootstrap.Alert.getOrCreateInstance(alert).close(); }, 5000);
	});
	document.querySelectorAll("[data-table-search]").forEach(function (input) {
		input.addEventListener("input", function () {
			const table = document.querySelector(input.dataset.tableSearch);
			if (!table) return;
			const query = input.value.toLowerCase();
			table.querySelectorAll("tbody tr").forEach(function (row) {
				row.hidden = query && !row.textContent.toLowerCase().includes(query);
			});
			const card = table.closest(".card");
			if (card) paginateCard(card);
		});
	});
	document.querySelectorAll("table.sortable thead th[data-sort]").forEach(function (th) {
		th.style.cursor = "pointer";
		th.title = "Sort";
		th.addEventListener("click", function () {
			const table = th.closest("table");
			const idx = Array.prototype.indexOf.call(th.parentNode.children, th);
			const asc = th.dataset.dir !== "asc";
			table.querySelectorAll("thead th").forEach(function (h) { delete h.dataset.dir; });
			th.dataset.dir = asc ? "asc" : "desc";
			const rows = Array.from(table.querySelectorAll("tbody tr"));
			rows.sort(function (a, b) {
				const av = (a.children[idx] ? a.children[idx].textContent.trim() : "").toLowerCase();
				const bv = (b.children[idx] ? b.children[idx].textContent.trim() : "").toLowerCase();
				const an = parseFloat(av.replace(/[^0-9.\-]/g, ""));
				const bn = parseFloat(bv.replace(/[^0-9.\-]/g, ""));
				let cmp = 0;
				if (!isNaN(an) && !isNaN(bn) && av.match(/[0-9]/) && bv.match(/[0-9]/)) cmp = an - bn;
				else cmp = av.localeCompare(bv);
				return asc ? cmp : -cmp;
			});
			const tb = table.querySelector("tbody");
			rows.forEach(function (r) { tb.appendChild(r); });
		});
	});
	function paginateCard(card) {
		const table = card.querySelector("table");
		if (!table) return;
		let page = parseInt(card.dataset.page || "1", 10);
		const per = 15;
		const rows = Array.from(table.querySelectorAll("tbody tr")).filter(function (r) { return !r.hidden; });
		const pages = Math.max(1, Math.ceil(rows.length / per));
		if (page > pages) page = pages;
		card.dataset.page = String(page);
		rows.forEach(function (r, i) {
			r.style.display = (i >= (page - 1) * per && i < page * per) ? "" : "none";
		});
		const info = card.querySelector("[data-page-info]");
		if (info) info.textContent = "Page " + page + " of " + pages + " · " + rows.length + " rows";
	}
	document.querySelectorAll(".card").forEach(function (card) {
		if (!card.querySelector("table") || !card.querySelector("[data-page-next]")) return;
		card.dataset.page = "1";
		paginateCard(card);
		card.querySelector("[data-page-next]").addEventListener("click", function () { card.dataset.page = String(parseInt(card.dataset.page, 10) + 1); paginateCard(card); });
		card.querySelector("[data-page-prev]").addEventListener("click", function () { card.dataset.page = String(Math.max(1, parseInt(card.dataset.page, 10) - 1)); paginateCard(card); });
	});
});