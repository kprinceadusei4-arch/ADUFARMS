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
	const body = document.body;
	const sidebar = document.getElementById("appSidebar");
	const sidebarCollapse = document.getElementById("sidebarCollapse");
	const sidebarScrim = document.getElementById("sidebarScrim");
	const savedSidebar = localStorage.getItem("adufarms-sidebar");
	if (savedSidebar === "collapsed" && window.matchMedia("(min-width: 801px)").matches) body.classList.add("sidebar-collapsed");
	if (sidebarCollapse) sidebarCollapse.addEventListener("click", function () {
		if (window.matchMedia("(max-width: 800px)").matches) { body.classList.toggle("sidebar-open"); return; }
		body.classList.toggle("sidebar-collapsed");
		localStorage.setItem("adufarms-sidebar", body.classList.contains("sidebar-collapsed") ? "collapsed" : "expanded");
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
	const popoverPairs = [["notificationToggle", "notificationPanel"], ["profileToggle", "profilePanel"], ["sidebarProfileToggle", "sidebarProfilePanel"]];
	popoverPairs.forEach(function (pair) {
		const toggle = document.getElementById(pair[0]);
		const panel = document.getElementById(pair[1]);
		if (!toggle || !panel) return;
		toggle.addEventListener("click", function (event) {
			event.stopPropagation();
			document.querySelectorAll(".popover-panel.open").forEach(function (openPanel) { if (openPanel !== panel) openPanel.classList.remove("open"); });
			panel.classList.toggle("open");
			toggle.setAttribute("aria-expanded", panel.classList.contains("open"));
		});
		panel.addEventListener("click", function (event) { event.stopPropagation(); });
	});
	document.addEventListener("click", function () { document.querySelectorAll(".popover-panel.open").forEach(function (panel) { panel.classList.remove("open"); }); });
	document.addEventListener("keydown", function (event) { if (event.key === "Escape") { body.classList.remove("sidebar-open"); document.querySelectorAll(".popover-panel.open").forEach(function (panel) { panel.classList.remove("open"); }); } });

	const modalElement = document.getElementById("actionConfirmModal");
	if (modalElement && typeof bootstrap !== "undefined") {
		const modal = new bootstrap.Modal(modalElement);
		const message = document.getElementById("actionConfirmMessage");
		const confirmButton = document.getElementById("actionConfirmButton");
		let pendingForm = null;
		document.querySelectorAll("form[onsubmit]").forEach(function (form) {
			const inlineHandler = form.getAttribute("onsubmit") || "";
			if (inlineHandler.includes("confirm")) {
				form.removeAttribute("onsubmit");
				form.dataset.confirm = form.action.includes("/restore") ? "Restore this record?" : "This action will reverse the record and retain its history. Continue?";
			}
		});
		document.querySelectorAll("form[data-confirm]").forEach(function (form) {
			form.addEventListener("submit", function (event) {
				if (form.dataset.confirmed === "true") { form.dataset.confirmed = "false"; return; }
				event.preventDefault(); pendingForm = form; message.textContent = form.dataset.confirm; modal.show();
			});
		});
		if (confirmButton) confirmButton.addEventListener("click", function () { if (!pendingForm) return; pendingForm.dataset.confirmed = "true"; modal.hide(); pendingForm.requestSubmit(); pendingForm = null; });
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
		});
	});
});