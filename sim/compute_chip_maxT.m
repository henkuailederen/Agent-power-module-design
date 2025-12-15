function [SiMax, SiMaxTbl] = compute_chip_maxT(model, P_igbt, P_fwd, h, varargin)
% COMPUTE_CHIP_MAXT  统计每个 Silicon 域（芯片）的最高温度及位置
%
%   [SiMax, SiMaxTbl] = compute_chip_maxT(model, P_igbt, P_fwd, h, ...
%                       'Dataset', 'dset1')
%
% 输出矩阵 SiMax 每行格式:
%   [域ID, x, y, Tmax_C, P_igbt, P_fwd, h]
%
% 说明:
%   - 假定 Silicon 材料在建模阶段使用标签 'mat_silicon'
%   - 温度场变量为 'T'，数据集默认为 'dset1'
%
% 输入:
%   model   : 已求解完成的 COMSOL Model 对象
%   P_igbt  : IGBT 总功率 [W]
%   P_fwd   : FWD  总功率 [W]
%   h       : 底面换热系数 [W/(m^2*K)]
%
% Name-Value 参数:
%   'Dataset' : 结果数据集名称，默认 'dset1'
%
% 输出:
%   SiMax    : double 矩阵 [nSi × 7]
%   SiMaxTbl : table 形式（可选），列名：
%              {'DomainID','x','y','Tmax_C','P_igbt','P_fwd','h'}
%

ip = inputParser;
ip.addParameter('Dataset','dset1', @(s)ischar(s)||isstring(s));
ip.parse(varargin{:});
dset = char(ip.Results.Dataset);

% --- 1) 获取所有 Silicon 域 ---
try
    domSi = model.material('mat_silicon').selection.entities(3);
catch
    error('未找到材料 "mat_silicon" 的选择集，请确认模型中已创建 Silicon 材料并分配域。');
end

domSi = unique(double(domSi(:)));
nSi   = numel(domSi);
if nSi == 0
    error('材料 "mat_silicon" 未分配到任何 3D 域。');
end

% --- 2) 对每个域评估最高温度及其位置 ---
SiMax = zeros(nSi, 7);  % [域ID, x, y, Tmax(°C), P_igbt, P_fwd, h]

for i = 1:nSi
    d = domSi(i);
    pd = mpheval(model, 'T', 'selection', d, ...
                 'edim','domain', 'dataset', dset);
    [TmaxK, idx] = max(pd.d1);
    xy           = pd.p(:,idx);
    SiMax(i,:) = [double(d), xy(1), xy(2), TmaxK - 273.15, P_igbt, P_fwd, h];
end

% --- 3) 可选：返回 table ---
if nargout > 1
    SiMaxTbl = array2table(SiMax, 'VariableNames', ...
        {'DomainID','x','y','Tmax_C','P_igbt','P_fwd','h'});
else
    SiMaxTbl = [];
end

fprintf('已统计 %d 个 Silicon 域的最高温度。\n', nSi);

end


