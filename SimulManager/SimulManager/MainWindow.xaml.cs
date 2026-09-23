using System.Windows;
using DevExpress.Xpf.Core;
using DevExpress.Xpf.WindowsUI;
using SimulManager.ViewModels;

namespace SimulManager
{
    public partial class MainWindow : ThemedWindow
    {
        public MainWindow()
        {
            InitializeComponent();
        }

        private MainViewModel Vm => DataContext as MainViewModel;

        /// <summary>햄버거 메뉴 네비게이션 — Tag(home/monitor/data/sequence)로 페이지 전환.</summary>
        private void Nav_Click(object sender, RoutedEventArgs e)
        {
            var button = sender as HamburgerMenuNavigationButton;
            var page = button?.Tag as string;
            if (page != null && Vm != null)
                Vm.CurrentPage = page;
        }
    }
}
