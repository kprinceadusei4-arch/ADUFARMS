document.addEventListener('DOMContentLoaded', function () {
  const printButton = document.getElementById('printInvoiceButton');

  if (printButton) {
    printButton.addEventListener('click', function () {
      window.print();
    });
  }
});
