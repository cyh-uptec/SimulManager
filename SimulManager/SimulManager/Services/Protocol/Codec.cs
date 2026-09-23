// 스케일·부호 변환 코덱 — docs/PROTOCOL_RULES.md §2 (backend/app/protocol/codec.py 포팅).
//
// - 부호: 방전(PCS→계통) +, 충전 − (전 링크 공통)
// - raw = round(물리값 × scale), INT16은 2의 보수
// - 32-bit(누적 전력량): big-endian 상위워드 우선
using System;

namespace SimulManager.Services.Protocol
{
    public static class Codec
    {
        public const int UInt16Max = 0xFFFF;
        public const int Int16Min = -32768;
        public const int Int16Max = 32767;

        /// <summary>물리값 → 16-bit raw (레지스터에 쓸 값).</summary>
        public static int Encode(double value, RegisterDef reg)
        {
            if (reg.DType == DType.Bit)
                return Math.Abs(value) > 1e-9 ? 1 : 0;
            // Python round()와 동일한 half-to-even — 크로스 검증 시 반값 케이스 정합
            long scaled = (long)Math.Round(value * reg.Scale, MidpointRounding.ToEven);
            if (reg.DType == DType.Int16)
            {
                if (scaled < Int16Min || scaled > Int16Max)
                    throw new ArgumentOutOfRangeException(null, reg + ": INT16 범위 초과 (scaled=" + scaled + ")");
                return (int)(scaled & UInt16Max); // 2의 보수
            }
            if (scaled < 0 || scaled > UInt16Max)
                throw new ArgumentOutOfRangeException(null, reg + ": UINT16 범위 초과 (scaled=" + scaled + ")");
            return (int)scaled;
        }

        /// <summary>16-bit raw → 물리값.</summary>
        public static double Decode(int raw, RegisterDef reg)
        {
            if (reg.DType == DType.Bit)
                return raw != 0 ? 1 : 0;
            if (raw < 0 || raw > UInt16Max)
                throw new ArgumentOutOfRangeException(null, reg + ": raw가 16-bit 범위 밖 (raw=" + raw + ")");
            int signed = (reg.DType == DType.Int16 && raw > Int16Max) ? raw - 0x10000 : raw;
            if (Math.Abs(reg.Scale - 1.0) < 1e-12)
                return signed;
            return signed / reg.Scale;
        }

        /// <summary>상위·하위 워드 → UINT32 (big-endian, 상위워드 우선).</summary>
        public static uint U32FromWords(int hi, int lo)
        {
            if (hi < 0 || hi > UInt16Max || lo < 0 || lo > UInt16Max)
                throw new ArgumentOutOfRangeException(null, "워드가 16-bit 범위 밖 (hi=" + hi + ", lo=" + lo + ")");
            return ((uint)hi << 16) | (uint)lo;
        }

        public static Tuple<int, int> WordsFromU32(uint value) =>
            Tuple.Create((int)((value >> 16) & UInt16Max), (int)(value & UInt16Max));

        // 누적 전력량(②IR 300033~300036): UINT32 결합 후 ×10 kWh
        public const double EnergyU32Scale = 10.0;

        public static double DecodeEnergyKwh(int hi, int lo) => U32FromWords(hi, lo) / EnergyU32Scale;

        public static Tuple<int, int> EncodeEnergyKwh(double kwh) =>
            WordsFromU32((uint)Math.Round(kwh * EnergyU32Scale, MidpointRounding.ToEven));
    }
}
