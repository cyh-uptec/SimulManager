using System.Windows;
using DevExpress.Xpf.Core;
using SciChart.Charting.Visuals;
using SimulManager.Services;
using SimulManager.ViewModels;

namespace SimulManager
{
    public partial class App : Application
    {
        private SimulatorHost _host;

        public App()
        {
            // Set this code once in App.xaml.cs or application startup
            SciChartSurface.SetRuntimeLicenseKey("B5zPkDJtqRBum9dH8GR1LyMLfAVUfYgVJBqh1ANZ7lh4GRBAgZwM9PShVWRYAP5JtMYox/MQNmBc5rbnUv4o8ntTGkOUX8X1HG4RsVOv1Z/qq8pyqPsV/DHeJGykvBVaXAo+800JRhF5TOezA0cnd5KHNUbrLK7UY8nRXuPAdz/NcX815A/eEWBmnJOFrM2XHJmP38Qb3EVOJU2oQyJvsbCyijdVMyqkNpAb6FGjj/YtbvQSEolspKjE13ckBzmEv7+haTh4f8uAOtVJ9ZXwXeEF3VNAiXwCRa9zp7JswgsQhr/gqwKYQ21J1cxZfT6AmQiCVG2Ri6/jC4bBLwgFHHq08jbJ0bn6lWsM8xkHC9wqbIzLwBMW8l8d7QAh0ewX95TWft3S5jrCEDrGEVrOI5RGy6qr0TAdE6xyzZ2AEXcluXkjHNsxSBK5pIhWX0sWmN3OtJ5tiOOr6BmR7hk9OcNtSAPkHdZZfi2vi8nIGQ==");
        }

        protected override void OnStartup(StartupEventArgs e)
        {
            ApplicationThemeHelper.ApplicationThemeName = Theme.Office2019ColorfulName;
            base.OnStartup(e);

            _host = new SimulatorHost();
            _host.Start();  // 링크①·③ Modbus 서버 + Heartbeat 감시 기동

            var window = new MainWindow { DataContext = new MainViewModel(_host) };
            MainWindow = window;
            window.Show();
        }

        protected override void OnExit(ExitEventArgs e)
        {
            _host?.Dispose();
            base.OnExit(e);
        }
    }
}
