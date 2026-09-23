// Excel(xlsx) 읽기 — DevExpress Office File API(Workbook) 이용.
// CSV가 기본 경로이며, xlsx는 보조 지원 (실패 시 CSV 변환 안내 예외).
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Runtime.CompilerServices;

namespace SimulManager.Services.Scenario
{
    internal static class XlsxReader
    {
        public static List<List<string>> ReadFirstSheet(byte[] content)
        {
            try
            {
                return ReadCore(content);
            }
            catch (Exception ex) when (!(ex is ArgumentException))
            {
                throw new ArgumentException(
                    "Excel 파일을 읽을 수 없습니다 — CSV로 변환해 업로드하세요 (" + ex.Message + ")");
            }
        }

        [MethodImpl(MethodImplOptions.NoInlining)]
        private static List<List<string>> ReadCore(byte[] content)
        {
            var rows = new List<List<string>>();
            using (var wb = new DevExpress.Spreadsheet.Workbook())
            {
                using (var ms = new MemoryStream(content))
                    wb.LoadDocument(ms, DevExpress.Spreadsheet.DocumentFormat.Xlsx);
                var ws = wb.Worksheets[0];
                var used = ws.GetUsedRange();
                int rowCount = used.RowCount, colCount = used.ColumnCount;
                for (int r = 0; r < rowCount; r++)
                {
                    var row = new List<string>(colCount);
                    for (int c = 0; c < colCount; c++)
                    {
                        var cell = used[r, c];
                        var v = cell.Value;
                        if (v.IsDateTime)
                            row.Add(v.DateTimeValue.ToString("yyyy-MM-dd HH:mm:ss"));
                        else if (v.IsNumeric)
                            row.Add(v.NumericValue.ToString(CultureInfo.InvariantCulture));
                        else
                            row.Add(v.TextValue ?? cell.DisplayText ?? "");
                    }
                    rows.Add(row);
                }
            }
            return rows;
        }
    }
}
