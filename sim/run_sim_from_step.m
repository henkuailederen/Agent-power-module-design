function [model, modelPath] = run_sim_from_step(stepFilename, P_igbt, P_fwd, h, run_id)
%% =====================================================================
%  run_sim_from_step.m  ―  COMSOL‑MATLAB 单次热分析工具函数
%  版本: 2025‑08‑06（由批量脚本简化）
%
%  功能:
%    - 从工程根目录下 data/step 中读取指定 STEP 文件
%    - 按给定 P_igbt、P_fwd 和 h 进行一次稳态热仿真
%    - 只完成求解，不再提取上表面温度场和各芯片最高温
%    - 将已求解的模型保存为 .mph 文件，便于后续独立后处理
%
%  输入参数:
%    stepFilename : STEP 文件名（带或不带 .step 后缀），位于 data/step 目录下
%    P_igbt       : IGBT 总功率 [W]，可选，默认 150
%    P_fwd        : FWD  总功率 [W]，可选，默认 100
%    h            : 底面换热系数 [W/(m^2*K)]，可选，默认 2000
%
%  输出:
%    model        : 已求解完成的 COMSOL Model 对象
%    modelPath    : 保存到磁盘的 .mph 文件完整路径（位于 data/sim_results 目录下）
%
%  说明:
%    - STEP 目录固定为工程根目录下 data/step
%    - 结果目录默认设置为 data/sim_results，用于保存 .mph 结果文件，
%      便于在后续独立调用 compute_chip_maxT 等函数进行后处理。
% =====================================================================

%% ---------- 工程相关路径：data/step 与 data/sim_results ----------
thisFile    = mfilename('fullpath');
simDir      = fileparts(thisFile);                % ...\Power Module Agent\sim
projectRoot = fileparts(simDir);                  % ...\Power Module Agent
stepDir     = fullfile(projectRoot, 'data', 'step');
csvDir      = fullfile(projectRoot, 'data', 'sim_results'); %#ok<NASGU>

if ~exist(stepDir, 'dir')
    error('STEP 目录不存在：%s', stepDir);
end
if ~exist(csvDir, 'dir')
    mkdir(csvDir);
end

%% ---------- 处理输入参数 ----------
if nargin < 1 || isempty(stepFilename)
    error('必须提供 STEP 文件名，例如 "xxx.step"（位于 data/step 目录下）。');
end

% 允许用户不写 .step 后缀
if ~endsWith(stepFilename, '.step', 'IgnoreCase', true)
    stepFilename = [stepFilename '.step'];
end

STEP = fullfile(stepDir, stepFilename);
if ~exist(STEP, 'file')
    error('在 %s 中未找到 STEP 文件：%s', stepDir, stepFilename);
end

if nargin < 2 || isempty(P_igbt), P_igbt = 150;  end
if nargin < 3 || isempty(P_fwd),  P_fwd  = 100;  end
if nargin < 4 || isempty(h),      h      = 2000; end
if nargin < 5
    run_id = [];
end

fprintf('\n=============================================================\n');
fprintf('单次仿真: %s\n', STEP);
fprintf('参数: P_igbt=%g W, P_fwd=%g W, h=%g W/(m^2*K)\n', P_igbt, P_fwd, h);
fprintf('STEP 目录: %s\n', stepDir);
fprintf('结果目录(预留): %s\n', csvDir);
fprintf('=============================================================\n');

%% -----------------------------------------------------------------
%  以下内容与原脚本基本一致，仅去掉循环与后处理
% ------------------------------------------------------------------
import com.comsol.model.*
import com.comsol.model.util.*

% 0. 建立模型、组件与几何
ModelUtil.showProgress(true);
ModelUtil.clear;
model = ModelUtil.create('Model');
model.component.create('comp1', true);      % true → 3‑D component
geom = model.component('comp1').geom.create('geom1', 3);

% 1. 导入 STEP，自动生成颜色选择
imp = geom.feature.create('imp1','Import');
imp.set('filename', STEP);
imp.set('createselection','on');
imp.set('selresult','on');
imp.set('selresultshow','dom');
imp.set('selcadcolordom','on');
imp.set('selcadcolorbnd','on');
geom.run;

% 2. 材料字典
matProp = struct( ...
  'Alumina', struct('rho',3960,'Cp',880,'k',30 , 'sig',1e-14), ...
  'Copper',  struct('rho',8960,'Cp',385,'k',400, 'sig',5.96e7), ...
  'SAC305',  struct('rho',7300,'Cp',230,'k',59 , 'sig',7.5e5 ), ...
  'Aluminum',struct('rho',2700,'Cp',900,'k',237, 'sig',3.77e7), ...
  'Silicon', struct('rho',2330,'Cp',700,'k',148, 'sig',1e-4 )  );

% 3. 遍历颜色域选择 → 匹配关键字 → 创建/绑定材料
tags        = arrayfun(@char, model.selection.tags, 'uni', false);
matCreated  = containers.Map;
DomList     = struct();

for sTag = tags(:)'
    tag = sTag{1};
    if ~endsWith(tag,'_dom');  continue; end
    key = '';
    low = lower(tag);
    if     contains(low,'solder');           key='SAC305';
    elseif contains(low,'die');              key='Silicon';
    elseif contains(low,'ceramics');         key='Alumina';
    elseif contains(low,{'zone','gate','bottom'}); key='Copper';
    elseif contains(low,'substrate');        key='Aluminum';
    else; continue;
    end
    if ~isfield(DomList,key);  DomList.(key) = []; end
    DomList.(key) = [DomList.(key), model.selection(tag).entities(3).'];
end

matKeys = fieldnames(DomList);
for i = 1:numel(matKeys)
    key    = matKeys{i};
    domIDs = unique(DomList.(key));
    if ~isKey(matCreated,key)
        matTag = ['mat_' lower(key)];
        mp     = matProp.(key);
        mat    = model.material.create(matTag,'Common','comp1');
        mat.label(key);
        def = mat.propertyGroup('def');
        def.set('density',             sprintf('%g[kg/m^3]', mp.rho));
        def.set('heatcapacity',        sprintf('%g[J/(kg*K)]', mp.Cp));
        def.set('thermalconductivity', sprintf('%g[W/(m*K)]', mp.k));
        def.set('electricconductivity',sprintf('%g[S/m]'    , mp.sig));
        matCreated(key) = matTag;
    end
    model.material(matCreated(key)).selection.set(domIDs);
end
fprintf('材料 %d 种，覆盖域 %d 个。\n', numel(matKeys), sum(structfun(@numel,DomList)));

% 4. 网格
mesh = model.component('comp1').mesh.create('mesh1','geom1');
mesh.autoMeshSize(4);
mesh.run;
fprintf('网格完成：%d elements\n', mesh.getNumElem('tet'));

% 5. 热传导物理场
physTag = 'ht';
try
    model.component('comp1').physics.create(physTag,'HeatTransferInSolids','geom1');
catch
    model.component('comp1').physics.create(physTag,'HeatTransfer','geom1');
end
ht = model.component('comp1').physics(physTag);
ht.selection.all;

% 6. 自动构建选择集
selDieFWD  = model.selection.create('sel_die_fwd' ,'Explicit');  selDieFWD.geom('geom1', 3);
selDieIGBT = model.selection.create('sel_die_igbt','Explicit');  selDieIGBT.geom('geom1', 3);
selSubBnd  = model.selection.create('sel_sub_bottom','Explicit'); selSubBnd.geom('geom1', 2);

for sTag = tags(:)'
    tag = sTag{1};
    if endsWith(tag,'_dom') && contains(lower(tag),'die')
        if contains(lower(tag),'fwd')
            selDieFWD.add(model.selection(tag).entities(3));
        elseif contains(lower(tag),'igbt')
            selDieIGBT.add(model.selection(tag).entities(3));
        end
    end
end

domSub = DomList.Aluminum;
bSubAll  = mphgetadj(model, 'geom1', 'boundary', 'domain', domSub);
zCent = zeros(size(bSubAll));
for kk = 1:numel(bSubAll)
    bid = bSubAll(kk);
    Praw = mphgetcoords(model,'geom1','boundary',bid);
    if size(Praw,1) == 3, P = Praw.'; end
    zCent(kk) = mean(P(:,3));
end
tolZ   = 1e-4*(max(zCent)-min(zCent));
zMin   = min(zCent);
bSub   = bSubAll(abs(zCent - zMin) <= tolZ);
selSubBnd.set(bSub);

% 7. 热源 & 对流散热边界
model.param.set('P_fwd' , sprintf('%g[W]', P_fwd)          , 'FWD 总功率');
model.param.set('P_igbt', sprintf('%g[W]', P_igbt)         , 'IGBT 总功率');
model.param.set('h_bottom', sprintf('%g[W/(m^2*K)]', h)    , '底面换热系数');
model.param.set('T_amb' , '298.15[K]'       , '环境温度 25°C');

hs1 = ht.feature.create('hs1','HeatSource',3);
hs1.selection.named('sel_die_fwd');
hs1.set('heatSourceType','HeatRate');
hs1.set('P0','P_fwd');

hs2 = ht.feature.create('hs2','HeatSource',3);
hs2.selection.named('sel_die_igbt');
hs2.set('heatSourceType','HeatRate');
hs2.set('P0','P_igbt');

hf1 = ht.feature.create('hf1','HeatFluxBoundary',2);
hf1.selection.named('sel_sub_bottom');
hf1.set('HeatFluxType','ConvectiveHeatFlux');
hf1.set('h'   ,'h_bottom');
hf1.set('Text','T_amb');

% 8. 求解（仅到此为止，不再做后处理）
stdy = model.study.create('std1');
stdy.feature.create('stat','Stationary');
stdy.run;

fprintf('仿真完成。\n');

% 9. 保存已求解模型到 data/sim_results 目录，便于后续独立后处理
%    命名规则：
%    - 如果提供了非空 run_id，则使用 run_id 作为前缀：<run_id>_thermal.mph；
%    - 否则回退为 <stepBase>_thermal.mph。
[~, stepBase, ~] = fileparts(stepFilename);
if ~isempty(run_id)
    baseName = char(run_id);
else
    baseName = stepBase;
end
modelFilename = sprintf('%s_thermal.mph', baseName);
modelPath = fullfile(csvDir, modelFilename);

mphsave(model, modelPath);
fprintf('已将求解后的模型保存到（覆盖模式）：%s\n', modelPath);

end


