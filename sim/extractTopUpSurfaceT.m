function R = extractTopUpSurfaceT(model, geomTag, varargin)
% 提取“朝上”的外表面温度（纯 MATLAB / LiveLink）
% 依赖：mphmeshstats, mphgetadj, mphinterp
% Name-Value:
%   'UpDir'  : 3x1 上方向，默认 [0;0;1]
%   'Thresh' : 点积阈值 τ，默认 0.1
%   'DataSet': 结果数据集，默认 'dset1'
%   'MeshTag': 网格标签，默认 'mesh1'

p = inputParser;
addParameter(p,'UpDir',[0;0;1]);
addParameter(p,'Thresh',0.1);
addParameter(p,'DataSet','dset1');
addParameter(p,'MeshTag','mesh1');
parse(p,varargin{:});
d = p.Results.UpDir(:); d = d / norm(d);
tau   = p.Results.Thresh;
dset  = p.Results.DataSet;
mesht = p.Results.MeshTag;

% —— 1) 读取网格（官方函数）——
[stats, data] = mphmeshstats(model, mesht);

% 可能存在多个 'tri' 集合，取元素数最多的那个
triIdx = find(strcmp(stats.types,'tri'));
if isempty(triIdx)
    error('网格中未找到三角形边界元素（types 无 ''tri''）。');
end
if numel(triIdx) > 1
    sizes = cellfun(@(E) size(E,2), data.elem(triIdx));
    [~, imax] = max(sizes);
    idTri = triIdx(imax);
else
    idTri = triIdx(1);
end

% 顶点坐标
V = double(data.vertex);              % 3×Nv

% 元素顶点索引（可能是 0-based，需要 +1）
Fraw = double(data.elem{idTri});      % (>=3)×Ntri
if ~isempty(Fraw) && min(Fraw(:)) == 0
    Fraw = Fraw + 1;
end
F = Fraw(1:3, :);                     % 只取角点
Ntri = size(F,2);

% 每个三角面对应的“边界几何实体 ID”（有些版本 0-based）
bndId = double(data.elementity{idTri});
if ~isempty(bndId) && min(bndId(:)) == 0
    bndId = bndId + 1;
end
bndId = bndId(:).';                   % 强制成 1×N 以避免隐式扩展

% —— 2) 外表面筛选：边界仅邻接 1 个域 —— 
maskExt = true(1, Ntri);              % 先假定全部为外表面
if numel(bndId) == Ntri
    ub = unique(bndId);
    isExt = false(size(ub));
    for k = 1:numel(ub)
        doms = mphgetadj(model, geomTag, 'domain', 'boundary', ub(k));
        isExt(k) = numel(doms) == 1;  % 外边界 ⇔ 仅 1 域
    end
    extB = ub(isExt);
    maskExt = ismember(bndId, extB);  % 1×Ntri 行向量
else
    warning('elementity 数量(%d) ≠ 三角面数(%d)，跳过外表面判别，默认全部视为外表面。', ...
            numel(bndId), Ntri);
end

% —— 3) 法向与“朝上”判据 —— 
v1 = V(:,F(2,:)) - V(:,F(1,:));
v2 = V(:,F(3,:)) - V(:,F(1,:));
Nvec = cross(v1', v2')';              % 3×Ntri
A2   = vecnorm(Nvec,2,1);             % 2×面积
n    = Nvec ./ A2;                    % 单位法向（按外向）
% 与上方向的点积（直接得到 1×Ntri 行向量）
score = n(1,:)*d(1) + n(2,:)*d(2) + n(3,:)*d(3);

% 统一为行向量，避免隐式扩展成 N×N
maskExt = maskExt(:).';
score   = score(:).';

keep = maskExt & (score > tau);       % 1×Ntri 合法逻辑掩码
if ~any(keep)
    error('筛选后无“朝上外表面”三角面（请调低 Thresh=%.3f 或检查 UpDir）。', tau);
end

Fkeep  = F(:, keep);
Akeep  = 0.5 * A2(keep);
C      = (V(:,Fkeep(1,:)) + V(:,Fkeep(2,:)) + V(:,Fkeep(3,:))) / 3;  % 3×K

% —— 4) 边界维度插值温度 —— 
T = mphinterp(model, 'T', 'coord', C, 'edim', 2, 'dataset', dset);
good = isfinite(T);
C = C(:,good); Akeep = Akeep(good); T = T(good);

% —— 5) 统计与输出 —— 
TavgK = sum(Akeep .* T) / sum(Akeep);
TmaxK = max(T);

R = struct('C',C,'T',T,'areas',Akeep,'TavgK',TavgK,'TmaxK',TmaxK,'keepMask',keep);
end
