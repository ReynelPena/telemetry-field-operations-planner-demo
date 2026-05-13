document.addEventListener('DOMContentLoaded', () => {
  const picker = document.getElementById('vehiclePicker');
  if (picker) {
    picker.addEventListener('change', () => {
      const selected = picker.options[picker.selectedIndex];
      const make = document.getElementById('vehicleMake');
      const model = document.getElementById('vehicleModel');
      const fuel = document.getElementById('fuelType');
      if (selected && selected.dataset.make) {
        make.value = selected.dataset.make || '';
        model.value = selected.dataset.model || '';
        fuel.value = selected.dataset.fuel || '';
      }
    });
  }
});
