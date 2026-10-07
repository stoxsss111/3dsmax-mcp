// MaxFast - fast geometry helpers for 3ds Max (C# 5 / .NET 4.x, Autodesk.Max wrapper).
// Every public entry point catches exceptions and returns an error string/empty result instead of
// throwing into Max. Scene objects are touched only on the calling (main) thread; heavy math
// (BVH build, ray casting) works on plain arrays and may run on background threads.
using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Runtime.InteropServices;
using Autodesk.Max;

namespace MFNS
{
    public static class Info
    {
        public static string Version() { return "MaxFast MFBUILD"; }
        public static string LastError = "";
    }

    internal static class Mx
    {
        public static IGlobal G { get { return GlobalInterface.Instance; } }
        public static int Now { get { return G.COREInterface.Time; } }

        static void TryDelete(object o)
        {
            if (o == null) return;
            try
            {
                var mi = o.GetType().GetMethod("MaybeAutoDelete");
                if (mi != null) { mi.Invoke(o, null); return; }
            }
            catch { }
        }

        // World-space triangles of one node: float[9*n]. zcut: keep only tris whose min z < zcut
        // (float.MaxValue = keep all). Returns null if the node has no mesh.
        public static float[] NodeTris(uint handle, float zcut, out string name)
        {
            name = "";
            IINode n = G.COREInterface.GetINodeByHandle(handle);
            if (n == null) return null;
            name = n.Name;
            int t = Now;
            IObjectState os = n.EvalWorldState(t, true);
            if (os == null || os.Obj == null) return null;
            IObject obj = os.Obj;
            if (obj.CanConvertToType(G.TriObjectClassID) == 0) return null;
            IObject conv = obj.ConvertToType(t, G.TriObjectClassID);
            ITriObject tri = conv as ITriObject;
            if (tri == null) return null;
            try
            {
                IMesh m = tri.Mesh;
                int nv = m.NumVerts, nf = m.NumFaces;
                if (nv == 0 || nf == 0) return new float[0];
                IMatrix3 tm = n.GetObjectTM(t, null);
                IPoint3 r0 = tm.GetRow(0), r1 = tm.GetRow(1), r2 = tm.GetRow(2), r3 = tm.GetRow(3);
                double a0 = r0.X, a1 = r0.Y, a2 = r0.Z, b0 = r1.X, b1 = r1.Y, b2 = r1.Z;
                double c0 = r2.X, c1 = r2.Y, c2 = r2.Z, d0 = r3.X, d1 = r3.Y, d2 = r3.Z;
                // vertices: one bulk copy from the mesh's own vertex array (read-only)
                float[] raw = new float[nv * 3];
                IntPtr vp = m.GetVertPtr(0).NativePointer;
                if (vp != IntPtr.Zero) Marshal.Copy(vp, raw, 0, nv * 3);
                else for (int i = 0; i < nv; i++) { IPoint3 p = m.GetVert(i); raw[i * 3] = p.X; raw[i * 3 + 1] = p.Y; raw[i * 3 + 2] = p.Z; }
                float[] W = new float[nv * 3];
                for (int i = 0; i < nv; i++)
                {
                    double x = raw[i * 3], y = raw[i * 3 + 1], z = raw[i * 3 + 2];
                    W[i * 3] = (float)(x * a0 + y * b0 + z * c0 + d0);
                    W[i * 3 + 1] = (float)(x * a1 + y * b1 + z * c1 + d1);
                    W[i * 3 + 2] = (float)(x * a2 + y * b2 + z * c2 + d2);
                }
                // faces: Face = {DWORD v[3]; DWORD smGroup; DWORD flags;} = 5 ints, bulk copy
                int[] fr = null;
                IList<IFace> F = m.Faces;
                IntPtr fp = F.Count > 0 ? F[0].NativePointer : IntPtr.Zero;
                if (fp != IntPtr.Zero) { fr = new int[nf * 5]; Marshal.Copy(fp, fr, 0, nf * 5); }
                List<float> outT = new List<float>(nf * 9);
                for (int f = 0; f < nf; f++)
                {
                    int ia, ib, ic;
                    if (fr != null) { ia = fr[f * 5]; ib = fr[f * 5 + 1]; ic = fr[f * 5 + 2]; }
                    else { IFace fc = F[f]; ia = (int)fc.GetVert(0); ib = (int)fc.GetVert(1); ic = (int)fc.GetVert(2); }
                    if (ia < 0 || ib < 0 || ic < 0 || ia >= nv || ib >= nv || ic >= nv) continue;
                    float za = W[ia * 3 + 2], zb = W[ib * 3 + 2], zc = W[ic * 3 + 2];
                    if (Math.Min(za, Math.Min(zb, zc)) >= zcut) continue;
                    outT.Add(W[ia * 3]); outT.Add(W[ia * 3 + 1]); outT.Add(za);
                    outT.Add(W[ib * 3]); outT.Add(W[ib * 3 + 1]); outT.Add(zb);
                    outT.Add(W[ic * 3]); outT.Add(W[ic * 3 + 1]); outT.Add(zc);
                }
                return outT.ToArray();
            }
            finally
            {
                if (!ReferenceEquals(conv, obj)) TryDelete(conv);
            }
        }
    }

    // ---------------------------------------------------------------- export
    public static class Geo
    {
        // Binary MFT1: "MFT1", int nObj; per obj: int nameBytes, utf8 name, byte flag(1 lawn), int nTris, float[9*nTris]
        public static string ExportTris(int[] handles, int[] flags, string outFile, float zcut)
        {
            try
            {
                int objs = 0; long tris = 0; var sw = System.Diagnostics.Stopwatch.StartNew();
                using (var bw = new BinaryWriter(File.Create(outFile)))
                {
                    bw.Write(Encoding.ASCII.GetBytes("MFT1"));
                    long countPos = bw.BaseStream.Position; bw.Write(0);
                    for (int i = 0; i < handles.Length; i++)
                    {
                        bool lawn = flags != null && i < flags.Length && flags[i] != 0;
                        string name;
                        float[] T = Mx.NodeTris((uint)handles[i], lawn ? float.MaxValue : zcut, out name);
                        if (T == null || T.Length == 0) continue;
                        byte[] nb = Encoding.UTF8.GetBytes(name ?? "");
                        bw.Write(nb.Length); bw.Write(nb); bw.Write((byte)(lawn ? 1 : 0));
                        int nt = T.Length / 9; bw.Write(nt);
                        byte[] buf = new byte[T.Length * 4]; Buffer.BlockCopy(T, 0, buf, 0, buf.Length); bw.Write(buf);
                        objs++; tris += nt;
                    }
                    bw.Seek((int)countPos, SeekOrigin.Begin); bw.Write(objs);
                }
                return "objects:" + objs + " tris:" + tris + " ms:" + sw.ElapsedMilliseconds;
            }
            catch (Exception e) { Info.LastError = e.ToString(); return "ERROR " + e.Message; }
        }
    }

    // ---------------------------------------------------------------- rays (BVH)
    public static class Rays
    {
        static float[] T;          // 9 floats per tri
        static int[] TObj;         // object index per tri
        static int[] Order;        // tri order after build
        static float[] NB;         // node bounds 6 per node
        static int[] NL;           // node: left child or -(start+1) for leaf
        static int[] NC;           // node: right child or count for leaf
        static int nodeCount;
        static string[] Names = new string[0];

        public static string Build(int[] handles)
        {
            try
            {
                var sw = System.Diagnostics.Stopwatch.StartNew();
                var all = new List<float>(); var obj = new List<int>(); var names = new List<string>();
                for (int i = 0; i < handles.Length; i++)
                {
                    string nm; float[] t = Mx.NodeTris((uint)handles[i], float.MaxValue, out nm);
                    names.Add(nm);
                    if (t == null) continue;
                    all.AddRange(t); int n = t.Length / 9; for (int k = 0; k < n; k++) obj.Add(i);
                }
                T = all.ToArray(); TObj = obj.ToArray(); Names = names.ToArray();
                BuildBVH();
                return "tris:" + TObj.Length + " nodes:" + nodeCount + " ms:" + sw.ElapsedMilliseconds;
            }
            catch (Exception e) { Info.LastError = e.ToString(); return "ERROR " + e.Message; }
        }

        static void BuildBVH()
        {
            int n = TObj.Length;
            Order = new int[n]; for (int i = 0; i < n; i++) Order[i] = i;
            float[] cen = new float[n * 3];
            for (int i = 0; i < n; i++) for (int a = 0; a < 3; a++) cen[i * 3 + a] = (T[i * 9 + a] + T[i * 9 + 3 + a] + T[i * 9 + 6 + a]) / 3f;
            int cap = Math.Max(1, 2 * n);
            NB = new float[cap * 6]; NL = new int[cap]; NC = new int[cap]; nodeCount = 0;
            if (n == 0) return;
            var stack = new Stack<int[]>(); // node, start, count
            int root = nodeCount++; stack.Push(new int[] { root, 0, n });
            while (stack.Count > 0)
            {
                int[] it = stack.Pop(); int node = it[0], s = it[1], c = it[2];
                float x0 = float.MaxValue, y0 = float.MaxValue, z0 = float.MaxValue, x1 = -float.MaxValue, y1 = -float.MaxValue, z1 = -float.MaxValue;
                float cx0 = float.MaxValue, cy0 = float.MaxValue, cz0 = float.MaxValue, cx1 = -float.MaxValue, cy1 = -float.MaxValue, cz1 = -float.MaxValue;
                for (int k = s; k < s + c; k++)
                {
                    int ti = Order[k];
                    for (int v = 0; v < 3; v++)
                    {
                        float px = T[ti * 9 + v * 3], py = T[ti * 9 + v * 3 + 1], pz = T[ti * 9 + v * 3 + 2];
                        if (px < x0) x0 = px; if (py < y0) y0 = py; if (pz < z0) z0 = pz;
                        if (px > x1) x1 = px; if (py > y1) y1 = py; if (pz > z1) z1 = pz;
                    }
                    float qx = cen[ti * 3], qy = cen[ti * 3 + 1], qz = cen[ti * 3 + 2];
                    if (qx < cx0) cx0 = qx; if (qy < cy0) cy0 = qy; if (qz < cz0) cz0 = qz;
                    if (qx > cx1) cx1 = qx; if (qy > cy1) cy1 = qy; if (qz > cz1) cz1 = qz;
                }
                NB[node * 6] = x0; NB[node * 6 + 1] = y0; NB[node * 6 + 2] = z0; NB[node * 6 + 3] = x1; NB[node * 6 + 4] = y1; NB[node * 6 + 5] = z1;
                if (c <= 4) { NL[node] = -(s + 1); NC[node] = c; continue; }
                float ex = cx1 - cx0, ey = cy1 - cy0, ez = cz1 - cz0;
                int axis = ex >= ey && ex >= ez ? 0 : (ey >= ez ? 1 : 2);
                float mid = axis == 0 ? (cx0 + cx1) * 0.5f : axis == 1 ? (cy0 + cy1) * 0.5f : (cz0 + cz1) * 0.5f;
                int i0 = s, i1 = s + c - 1;
                while (i0 <= i1)
                {
                    if (cen[Order[i0] * 3 + axis] < mid) i0++;
                    else { int tmp = Order[i0]; Order[i0] = Order[i1]; Order[i1] = tmp; i1--; }
                }
                int lc = i0 - s;
                if (lc == 0 || lc == c) lc = c / 2; // degenerate split
                if (lc == c / 2 && (i0 - s == 0 || i0 - s == c))
                {
                    // sort by axis to make the half split meaningful
                    Array.Sort(Order, s, c, new AxisCmp(cen, axis));
                }
                int L = nodeCount++, R = nodeCount++;
                NL[node] = L; NC[node] = R;
                stack.Push(new int[] { L, s, lc }); stack.Push(new int[] { R, s + lc, c - lc });
            }
        }

        class AxisCmp : IComparer<int>
        {
            float[] cen; int ax;
            public AxisCmp(float[] c, int a) { cen = c; ax = a; }
            public int Compare(int x, int y) { return cen[x * 3 + ax].CompareTo(cen[y * 3 + ax]); }
        }

        static bool HitBox(int node, double ox, double oy, double oz, double ix, double iy, double iz, double tmax)
        {
            double t0 = 0, t1 = tmax;
            double a = (NB[node * 6] - ox) * ix, b = (NB[node * 6 + 3] - ox) * ix; if (a > b) { double q = a; a = b; b = q; }
            if (a > t0) t0 = a; if (b < t1) t1 = b; if (t0 > t1) return false;
            a = (NB[node * 6 + 1] - oy) * iy; b = (NB[node * 6 + 4] - oy) * iy; if (a > b) { double q = a; a = b; b = q; }
            if (a > t0) t0 = a; if (b < t1) t1 = b; if (t0 > t1) return false;
            a = (NB[node * 6 + 2] - oz) * iz; b = (NB[node * 6 + 5] - oz) * iz; if (a > b) { double q = a; a = b; b = q; }
            if (a > t0) t0 = a; if (b < t1) t1 = b; return t0 <= t1;
        }

        // closest hit along ray; returns t (or -1) and tri index
        static double Cast1(double ox, double oy, double oz, double dx, double dy, double dz, double tmax, out int hitTri)
        {
            hitTri = -1; if (nodeCount == 0) return -1;
            double ix = 1.0 / (Math.Abs(dx) < 1e-12 ? 1e-12 : dx), iy = 1.0 / (Math.Abs(dy) < 1e-12 ? 1e-12 : dy), iz = 1.0 / (Math.Abs(dz) < 1e-12 ? 1e-12 : dz);
            double best = tmax; int[] st = new int[128]; int sp = 0; st[sp++] = 0;
            while (sp > 0)
            {
                int node = st[--sp];
                if (!HitBox(node, ox, oy, oz, ix, iy, iz, best)) continue;
                if (NL[node] < 0)
                {
                    int s = -NL[node] - 1, c = NC[node];
                    for (int k = s; k < s + c; k++)
                    {
                        int ti = Order[k]; int o9 = ti * 9;
                        double e1x = T[o9 + 3] - T[o9], e1y = T[o9 + 4] - T[o9 + 1], e1z = T[o9 + 5] - T[o9 + 2];
                        double e2x = T[o9 + 6] - T[o9], e2y = T[o9 + 7] - T[o9 + 1], e2z = T[o9 + 8] - T[o9 + 2];
                        double px = dy * e2z - dz * e2y, py = dz * e2x - dx * e2z, pz = dx * e2y - dy * e2x;
                        double det = e1x * px + e1y * py + e1z * pz;
                        if (Math.Abs(det) < 1e-12) continue;
                        double inv = 1.0 / det;
                        double tx = ox - T[o9], ty = oy - T[o9 + 1], tz = oz - T[o9 + 2];
                        double u = (tx * px + ty * py + tz * pz) * inv; if (u < -1e-9 || u > 1 + 1e-9) continue;
                        double qx = ty * e1z - tz * e1y, qy = tz * e1x - tx * e1z, qz = tx * e1y - ty * e1x;
                        double v = (dx * qx + dy * qy + dz * qz) * inv; if (v < -1e-9 || u + v > 1 + 1e-9) continue;
                        double tt = (e2x * qx + e2y * qy + e2z * qz) * inv;
                        if (tt > 1e-6 && tt < best) { best = tt; hitTri = ti; }
                    }
                }
                else
                {
                    if (sp + 2 > st.Length) Array.Resize(ref st, st.Length * 2);
                    st[sp++] = NL[node]; st[sp++] = NC[node];
                }
            }
            return hitTri >= 0 ? best : -1;
        }

        // rays: o[3n], d[3n] (normalized or not; t is in units of |d|). Returns float[2n]: t (-1 miss), object index.
        public static float[] Cast(float[] o, float[] d, float tmax)
        {
            try
            {
                int n = o.Length / 3; float[] r = new float[n * 2];
                Parallel.For(0, n, i =>
                {
                    int ht; double t = Cast1(o[i * 3], o[i * 3 + 1], o[i * 3 + 2], d[i * 3], d[i * 3 + 1], d[i * 3 + 2], tmax, out ht);
                    r[i * 2] = (float)t; r[i * 2 + 1] = ht >= 0 ? TObj[ht] : -1;
                });
                return r;
            }
            catch (Exception e) { Info.LastError = e.ToString(); return new float[0]; }
        }

        // downward rays from zFrom at xy[2n]: returns hit z (NaN miss) and object index
        public static float[] Down(float[] xy, float zFrom)
        {
            int n = xy.Length / 2; float[] o = new float[n * 3], d = new float[n * 3];
            for (int i = 0; i < n; i++) { o[i * 3] = xy[i * 2]; o[i * 3 + 1] = xy[i * 2 + 1]; o[i * 3 + 2] = zFrom; d[i * 3 + 2] = -1; }
            float[] r = Cast(o, d, float.MaxValue);
            for (int i = 0; i < n && r.Length == n * 2; i++) r[i * 2] = r[i * 2] < 0 ? float.NaN : zFrom - r[i * 2];
            return r;
        }

        public static string ObjectName(int i) { return (i >= 0 && i < Names.Length) ? Names[i] : ""; }
        public static void Clear() { T = null; TObj = null; Order = null; NB = null; NL = null; NC = null; nodeCount = 0; GC.Collect(); }
    }

    // ---------------------------------------------------------------- grass cards
    public static class Grass
    {
        // proto: 6 verts (rel), 2 quads (local idx 1-based), 8 uv (u,v)
        static float[][] PV = new float[0][]; static int[][] PF = new int[0][]; static float[][] PU = new float[0][];

        public static int LoadProtos(string file)
        {
            try
            {
                var pv = new List<float[]>(); var pf = new List<int[]>(); var pu = new List<float[]>();
                foreach (string line in File.ReadAllLines(file))
                {
                    string[] s = line.Split(new char[] { ' ' }, StringSplitOptions.RemoveEmptyEntries);
                    if (s.Length < 42) continue;
                    var c = System.Globalization.CultureInfo.InvariantCulture;
                    float[] v = new float[18]; for (int k = 0; k < 18; k++) v[k] = float.Parse(s[k], c);
                    int[] f = new int[8]; for (int k = 0; k < 8; k++) f[k] = (int)float.Parse(s[18 + k], c);
                    float[] u = new float[16]; for (int k = 0; k < 16; k++) u[k] = float.Parse(s[26 + k], c);
                    pv.Add(v); pf.Add(f); pu.Add(u);
                }
                PV = pv.ToArray(); PF = pf.ToArray(); PU = pu.ToArray();
                return PV.Length;
            }
            catch (Exception e) { Info.LastError = e.ToString(); return -1; }
        }

        // fill the mesh of an Editable_Mesh node (created by MAXScript) with cards [start, start+count) of binFile
        public static string BuildChunk(int nodeHandle, string binFile, int start, int count)
        {
            try
            {
                if (PV.Length == 0) return "ERROR no protos";
                var sw = System.Diagnostics.Stopwatch.StartNew();
                long total; float[] D;
                using (var fs = File.OpenRead(binFile))
                {
                    total = fs.Length / 24;
                    int nc = (int)Math.Max(0, Math.Min(count, total - start));
                    if (nc <= 0) return "empty";
                    fs.Seek((long)start * 24, SeekOrigin.Begin);
                    byte[] b = new byte[nc * 24]; int got = 0; while (got < b.Length) { int r = fs.Read(b, got, b.Length - got); if (r <= 0) break; got += r; }
                    D = new float[nc * 6]; Buffer.BlockCopy(b, 0, D, 0, b.Length);
                }
                int ncards = D.Length / 6, np = PV.Length;
                IINode n = Mx.G.COREInterface.GetINodeByHandle((uint)nodeHandle);
                if (n == null) return "ERROR node";
                ITriObject tri = n.ObjectRef as ITriObject;
                if (tri == null) return "ERROR node is not an Editable_Mesh";
                IMesh m = tri.Mesh;
                m.SetNumVerts(ncards * 6, false, false);
                m.SetNumFaces(ncards * 4, false, false);
                m.SetNumTVerts(ncards * 8, false);
                m.SetNumTVFaces(ncards * 4, false, 0);
                IList<IFace> F = m.Faces; IList<ITVFace> TF = m.TvFace;
                for (int c = 0; c < ncards; c++)
                {
                    float x = D[c * 6], y = D[c * 6 + 1], z = D[c * 6 + 2], ang = D[c * 6 + 3], sc = D[c * 6 + 4], u = D[c * 6 + 5];
                    int pi = Math.Min(np - 1, (int)(u * np)); if (pi < 0) pi = 0;
                    float[] v = PV[pi]; int[] f = PF[pi]; float[] uv = PU[pi];
                    double ca = Math.Cos(ang * Math.PI / 180.0), sa = Math.Sin(ang * Math.PI / 180.0);
                    int vb = c * 6;
                    for (int k = 0; k < 6; k++)
                    {
                        double rx = v[k * 3], ry = v[k * 3 + 1], rz = v[k * 3 + 2];
                        m.SetVert(vb + k, (float)(x + (rx * ca - ry * sa) * sc), (float)(y + (rx * sa + ry * ca) * sc), (float)(z + rz * sc));
                    }
                    int tb = c * 8, fb = c * 4;
                    for (int q = 0; q < 2; q++)
                    {
                        for (int j = 0; j < 4; j++) m.SetTVert(tb + q * 4 + j, uv[q * 8 + j * 2], uv[q * 8 + j * 2 + 1], 0f);
                        int a = vb + f[q * 4] - 1, b2 = vb + f[q * 4 + 1] - 1, c2 = vb + f[q * 4 + 2] - 1, d2 = vb + f[q * 4 + 3] - 1;
                        int ta = tb + q * 4;
                        IFace f1 = F[fb + q * 2]; f1.SetVerts(a, b2, c2); f1.Flags = 3u; f1.SmGroup = 0;   // edges A,B visible; C (diagonal) hidden
                        IFace f2 = F[fb + q * 2 + 1]; f2.SetVerts(a, c2, d2); f2.Flags = 6u; f2.SmGroup = 0; // edges B,C visible; A hidden
                        TF[fb + q * 2].SetTVerts(ta, ta + 1, ta + 2);
                        TF[fb + q * 2 + 1].SetTVerts(ta, ta + 2, ta + 3);
                    }
                }
                m.InvalidateGeomCache(); m.InvalidateTopologyCache();
                return "cards:" + ncards + " ms:" + sw.ElapsedMilliseconds;
            }
            catch (Exception e) { Info.LastError = e.ToString(); return "ERROR " + e.Message; }
        }
    }

    // ---------------------------------------------------------------- background MAXScript job queue
    public static class Jobs
    {
        static System.Windows.Forms.Timer timer;
        static Queue<string> q = new Queue<string>();
        static List<string> log = new List<string>();
        static bool busy = false; static int done = 0, total = 0; static string state = "idle";

        public static string Start(string[] tasks, int intervalMs)
        {
            try
            {
                q.Clear(); log.Clear(); done = 0; total = tasks.Length; state = "running";
                foreach (string t in tasks) q.Enqueue(t);
                if (timer == null) { timer = new System.Windows.Forms.Timer(); timer.Tick += Tick; }
                timer.Interval = Math.Max(10, intervalMs); timer.Start();
                return "queued " + total;
            }
            catch (Exception e) { Info.LastError = e.ToString(); return "ERROR " + e.Message; }
        }

        static void Tick(object sender, EventArgs e)
        {
            if (busy) return;
            busy = true;
            try
            {
                if (q.Count == 0) { timer.Stop(); if (state == "running") state = "done"; return; }
                string task = q.Dequeue();
                string wrapped = "try ((" + task + ") as string) catch (\"ERROR: \" + (getCurrentException()))";
                string r = ManagedServices.MaxscriptSDK.ExecuteStringMaxscriptQuery(wrapped, ManagedServices.MaxscriptSDK.ScriptSource.NotSpecified);
                done++;
                log.Add(r ?? "");
                if (r != null && r.StartsWith("ERROR:")) { state = "error"; q.Clear(); timer.Stop(); }
            }
            catch (Exception ex) { state = "error"; log.Add("EXC " + ex.Message); q.Clear(); if (timer != null) timer.Stop(); }
            finally { busy = false; }
        }

        public static string Status(int lastN)
        {
            var sb = new StringBuilder();
            sb.Append(state).Append(' ').Append(done).Append('/').Append(total).Append(" queued:").Append(q.Count);
            for (int i = Math.Max(0, log.Count - Math.Max(0, lastN)); i < log.Count; i++) sb.Append('\n').Append(log[i]);
            return sb.ToString();
        }
        public static string Cancel() { q.Clear(); if (timer != null) timer.Stop(); state = "cancelled"; return Status(1); }
    }
}
