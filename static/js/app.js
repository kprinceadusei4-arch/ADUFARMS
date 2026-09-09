document.addEventListener("DOMContentLoaded", function () {
	const modalElement = document.getElementById("actionConfirmModal");
	if (!modalElement || typeof bootstrap === "undefined") return;

	const modal = new bootstrap.Modal(modalElement);
	const message = document.getElementById("actionConfirmMessage");
	const confirmButton = document.getElementById("actionConfirmButton");
	let pendingForm = null;

	document.querySelectorAll("form[onsubmit]").forEach(function (form) {
		const inlineHandler = form.getAttribute("onsubmit") || "";
		if (inlineHandler.includes("confirm")) {
			form.removeAttribute("onsubmit");
			if (form.action.includes("/delete")) {
				form.dataset.confirm = "This action will delete the record. Do you want to continue?";
			} else if (form.action.includes("/restore")) {
				form.dataset.confirm = "Restore this record?";
			} else {
				form.dataset.confirm = "Are you sure you want to continue?";
			}
		}
	});

	document.querySelectorAll("form[data-confirm]").forEach(function (form) {
		form.addEventListener("submit", function (event) {
			if (form.dataset.confirmed === "true") {
				form.dataset.confirmed = "false";
				return;
			}
			event.preventDefault();
			pendingForm = form;
			message.textContent = form.dataset.confirm;
			modal.show();
		});
	});

	confirmButton.addEventListener("click", function () {
		if (!pendingForm) return;
		pendingForm.dataset.confirmed = "true";
		modal.hide();
		pendingForm.requestSubmit();
		pendingForm = null;
	});

	modalElement.addEventListener("hidden.bs.modal", function () {
		pendingForm = null;
	});
});