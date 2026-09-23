// 시뮬레이션 설정 다이얼로그 — frontend/src/SettingsModal.tsx 포팅.
// 대기(IDLE) 상태에서만 저장 가능. 저장 시 user_settings.json 영속화 + 레지스터 갱신.
using System;
using System.Globalization;
using System.Windows;
using System.Windows.Threading;
using DevExpress.Xpf.Core;
using SimulManager.Services;

namespace SimulManager.Views
{
    public partial class SettingsDialog : ThemedWindow
    {
        private readonly SimulatorHost _host;
        private readonly bool _editable;

        public SettingsDialog(SimulatorHost host, bool editable)
        {
            InitializeComponent();
            _host = host;
            _editable = editable;

            var sim = host.Config.Simulation;
            StartBox.Text = sim.StartVirtualTime.ToString("yyyy-MM-dd HH:mm");
            DaysBox.Text = sim.Days.ToString();
            AccelBox.Text = sim.AccelSecondsPer15Min.ToString("0.##");
            InitialSocBox.Text = sim.InitialSoc.ToString("0.#");
            SocMaxBox.Text = sim.SocMax.ToString("0.#");
            SocMinBox.Text = sim.SocMin.ToString("0.#");
            ChargeBox.Text = sim.ChargeLimitKw.ToString("0.#");
            DischargeBox.Text = sim.DischargeLimitKw.ToString("0.#");

            if (!editable)
            {
                NoticeBar.Visibility = Visibility.Visible;
                SaveButton.IsEnabled = false;
            }
        }

        private void Close_Click(object sender, RoutedEventArgs e) => Close();

        private void Save_Click(object sender, RoutedEventArgs e)
        {
            ErrorText.Visibility = Visibility.Collapsed;
            SavedText.Visibility = Visibility.Collapsed;
            if (!_editable) return;
            try
            {
                var sim = new SimulationConfig
                {
                    StartVirtualTime = ParseDate(StartBox.Text),
                    Days = int.Parse(DaysBox.Text.Trim(), CultureInfo.InvariantCulture),
                    AccelSecondsPer15Min = ParseDouble(AccelBox.Text),
                    InitialSoc = ParseDouble(InitialSocBox.Text),
                    SocMax = ParseDouble(SocMaxBox.Text),
                    SocMin = ParseDouble(SocMinBox.Text),
                    ChargeLimitKw = ParseDouble(ChargeBox.Text),
                    DischargeLimitKw = ParseDouble(DischargeBox.Text),
                };
                _host.ApplySettings(sim);
                SavedText.Visibility = Visibility.Visible;
                var timer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(700) };
                timer.Tick += (s2, e2) => { timer.Stop(); Close(); };
                timer.Start();
            }
            catch (FormatException)
            {
                ShowError("입력값을 확인하세요 — 숫자·시각 형식이 올바르지 않습니다");
            }
            catch (ArgumentException ex) { ShowError(ex.Message); }
            catch (InvalidOperationException ex) { ShowError(ex.Message); }
        }

        private void ShowError(string message)
        {
            ErrorText.Text = message;
            ErrorText.Visibility = Visibility.Visible;
        }

        private static DateTime ParseDate(string text)
        {
            DateTime dt;
            if (!DateTime.TryParse(text.Trim(), CultureInfo.InvariantCulture, DateTimeStyles.None, out dt))
                throw new FormatException();
            return dt;
        }

        private static double ParseDouble(string text) =>
            double.Parse(text.Trim(), CultureInfo.InvariantCulture);
    }
}
